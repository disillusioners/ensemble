# Phase 6: Project Experience Migration

## Objective

Migrate the **project-level** experience system from file-based memory (`.agents/{agent-id}/memories/`, `access_memory` tool, `inner_soul` memory/knowledge targets) to the new RAG knowledge system. Update agent markdown files to reference `explore()`/`experience()` for project knowledge. **Agent core memory** (`agents/{agent}/memory.md`, `agents/{agent}/soul.md`, `agents/{agent}/rule.md`) is PART OF THE AGENT DEFINITION and must remain completely untouched. Preserve `inner_soul` self-modification features (soul, user, workflow targets). Provide migration documentation and optional migration script.

## Coupling

- **Depends on**: Phase 3 (knowledge tools), Phase 4 (explorer), Phase 5 (experiencer)
- **Coupling type**: loose — only updates tool internals and markdown files
- **Shared files with other phases**:
  - Agent markdown files in `agents/*/` — **only knowledge.md and workflow.md updated** (soul.md, rule.md, memory.md as agent definition are NOT touched)
  - `daemon/tools/access_memory.py` — deprecated
  - `daemon/tools/inner_soul.py` — modified (redirect memory/knowledge targets to experience())
  - `daemon/tools/instance.py` — no changes needed (knowledge tools already added in Phase 3)
- **Why this coupling**: Migration touches all agent definitions but doesn't change Phase 1-5 code

## Context

### Current File-Based Memory System

> **⚠️ CRITICAL DISTINCTION**: There are TWO different memory systems. This migration ONLY affects project-level storage.

**Project-Level Storage (BEING REPLACED with RAG):**
```
<project-working-dir>/.agents/{agent-id}/memories/
├── 2026-04-01-postgresql-setup.md      ← project-specific knowledge
├── 2026-04-05-api-rate-limiting.md     ← learned while working on this project
└── ...

Shared (project-level):
.agents/shared/context.md                ← project state and goals
.agents/shared/conventions.md            ← project conventions
.agents/shared/planning/                 ← feature plans
```

**Agent Core Memory (KEEP AS-IS — DO NOT TOUCH):**
```
agents/{agent}/memory.md                 ← agent's behavioral knowledge (part of definition)
agents/{agent}/soul.md                   ← agent's identity and personality
agents/{agent}/rule.md                   ← agent's rules and guidelines
agents/{agent}/workflow.md               ← agent's workflow patterns
agents/{agent}/memories/                 ← agent's core memories (if any)
```

### Tools Affected

| Tool | Action | Reason |
|------|--------|--------|
| `access_memory` | **Deprecate** | Replaced by `explore()` |
| `inner_soul` (memory/knowledge targets) | **Redirect to experience()** | Semantic classification routes knowledge to memories/ |
| `inner_soul` (self-modification) | **Keep as-is** | soul, user, workflow targets unchanged |

### inner_soul Semantic Classification System (C3 Fix)

The `inner_soul` tool does NOT receive explicit `intent`/`target` parameters in most calls. Instead, it uses `_classify_request()` which runs regex pattern matching against the request text to classify into 15 types. Each classification type maps to target files:

| Classification Type | Targets (file) | Should Redirect to RAG? |
|---------------------|----------------|------------------------|
| `identity` | `["soul"]` | ❌ No — self-modification |
| `personality` | `["soul", "user"]` | ❌ No — self-modification |
| `user_preference` | `["user"]` | ❌ No — self-modification |
| `user_identity` | `["user"]` | ❌ No — self-modification |
| `knowledge` | `["memory", "memories"]` | ✅ Yes — project knowledge |
| `pattern` | `["memories"]` | ✅ Yes — observed patterns |
| `workflow` | `["workflow"]` | ❌ No — self-modification |
| `event` | `["memories"]` | ✅ Yes — events/observations |
| `skill` | `["memories"]` | ✅ Yes — learned skills |
| `mistake` | `["memories"]` | ✅ Yes — lessons learned |
| `project_knowledge` | `["REJECT"]` | ✅ Yes — NOW accepted (was rejected before) |

Additionally, explicit parameter calls route as:
| Intent + Target | Where it goes | Should Redirect? |
|-----------------|---------------|-----------------|
| `intent="remember"` (no target) | `["memories"]` | ✅ Yes |
| `intent="learn"` (no target) | `["memories", "memory"]` | ✅ Yes |
| `intent="change"` + any target | Target as specified | ❌ Only if target is "memory"/"memories" |

**The redirect rule**: If the resolved target list contains ONLY `"memories"` and/or `"memory"` (no `"soul"`, `"user"`, or `"workflow"`), redirect to `experience()`. If the target list is mixed (e.g., `["soul", "user"]` from personality), process normally.

### Agents to Update

All agents with `self` in their tool allow list:
- coder, leader, planner, reviewer, tester, approver, jober, giter, tidier
- _mother, _baby_template

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update inner_soul tool | Add classification-aware redirect for memory/knowledge targets | `daemon/tools/inner_soul.py` |
| 2 | Deprecate access_memory tool | Add deprecation notice, return helpful message | `daemon/tools/access_memory.py` |
| 3 | Update agent markdown files | Add knowledge section, deprecate memory references | `agents/*/{memory,knowledge,soul,rule,workflow}.md` |
| 4 | Update meta.json files | Add "knowledge" to all agents' tool allow lists | `agents/*/meta.json` |
| 5 | Create migration guide | Document what changed, how to migrate | `docs/migration/v1-knowledge-migration.md` |
| 6 | Create migration script | Optional script to bulk-import existing memories into RAG | `scripts/migrate_memory_to_rag.py` |
| 7 | Update configuration docs | Document RAG env vars and setup | `docs/configuration/rag-configuration.md` |

### Task 6.1: Update inner_soul Tool — Classification-Aware Redirect (C3 Fix)

**File**: `daemon/tools/inner_soul.py` (MODIFIED)

The migration intercepts at the **classification + target resolution** level, NOT at the parameter level. The logic works as follows:

**Step 1: Classification happens first** (existing `_classify_request()` — unchanged)

**Step 2: Target resolution** (existing logic in `create_inner_soul_tool()` — unchanged)

**Step 3: NEW — Check if targets should redirect to RAG** (new function `_should_redirect_to_rag()`)

**Step 4: If redirect → return guidance to use `experience()`**

**Step 5: If not redirect → execute existing update logic** (unchanged)

#### Implementation Details

**Add redirect detection function** (new, near top of file after CLASSIFICATION_RULES):

```python
# Targets that are handled by the RAG knowledge system
_RAG_TARGETS = {"memories", "memory"}

# Classification types that are knowledge-oriented (not self-modification)
_KNOWLEDGE_CLASSIFICATIONS = {
    "knowledge", "pattern", "event", "skill", "mistake", "project_knowledge"
}


def _should_redirect_to_rag(
    targets: list[str],
    classification: dict,
    explicit_target: bool,
) -> bool:
    """Determine if a request should be redirected to experience() instead of file-based memory.

    Redirect when:
    1. ALL resolved targets are RAG targets (memories/memory), AND
    2. The classification is knowledge-oriented (not identity/personality/workflow)

    Do NOT redirect when:
    - Any target is soul/user/workflow (self-modification)
    - The request is identity/personality/user-related
    - The classification is "project_knowledge" with REJECT target (special case — redirect)

    Args:
        targets: The resolved list of target strings.
        classification: The classification dict from _classify_request().
        explicit_target: Whether the user explicitly specified a target.

    Returns:
        True if request should redirect to experience().
    """
    class_type = classification.get("type", "")

    # Filter out "REJECT" from multi-match target merging.
    # When _classify_request() matches multiple types, targets can include
    # "REJECT" (from project_knowledge) alongside valid targets like
    # ["memory", "memories", "REJECT"]. Without filtering, the all() check
    # fails and the request falls through incorrectly.
    actual_targets = [t for t in targets if t != "REJECT"]

    # Special case: project_knowledge was REJECTED before — now redirect to RAG
    if class_type == "project_knowledge":
        return True

    # Check if ALL resolved targets are RAG-managed
    if not actual_targets or not all(t in _RAG_TARGETS for t in actual_targets):
        return False  # Has soul/user/workflow — self-modification

    # Check if classification is knowledge-oriented
    if class_type in _KNOWLEDGE_CLASSIFICATIONS:
        return True

    # Default/event classification with memories target — redirect
    if class_type == "event":
        return True

    return False
```

**Modify `create_inner_soul_tool()`** — Add redirect check after target resolution:

The existing flow in `create_inner_soul_tool()` (lines ~197-257):

```python
def inner_soul(content=None, request=None, intent=None, target=None):
    actual_request = request or content or ""
    # ... validation ...
    classification = _classify_request(actual_request)

    # CRITICAL: Check for project_knowledge classification BEFORE processing
    if classification["type"] == "project_knowledge":
        return _format_rejection(actual_request, classification)  # ← CHANGE THIS

    # Determine targets
    if target:
        targets = [target]
    elif intent == "remember" and not target:
        targets = ["memories"]
    elif intent == "learn" and not target:
        targets = ["memories", "memory"]
    else:
        targets = classification["targets"]

    # ← INSERT REDIRECT CHECK HERE (before _execute_update loop)

    # Execute updates
    results = []
    for t in targets:
        result = _execute_update(...)
```

**Replace the project_knowledge rejection and add redirect after target resolution:**

```python
def inner_soul(content=None, request=None, intent=None, target=None):
    actual_request = request or content or ""
    if not actual_request:
        return "ERROR: Must provide 'request' or 'content' parameter"
    if len(actual_request) > 2000:
        return "ERROR: Request exceeds 2000 character limit"

    growth_rules = _load_growth_rules(agent_path)
    classification = _classify_request(actual_request)

    # Determine targets (EXISTING LOGIC — unchanged)
    if target:
        targets = [target]
    elif intent == "remember" and not target:
        targets = ["memories"]
    elif intent == "learn" and not target:
        targets = ["memories", "memory"]
    else:
        targets = classification["targets"]

    # --- NEW: Check if this should redirect to RAG ---
    if _should_redirect_to_rag(targets, classification, explicit_target=bool(target)):
        return _format_rag_redirect(actual_request, classification, targets)

    # --- EXISTING: Self-modification path (unchanged) ---
    # Execute updates
    results = []
    for t in targets:
        result = _execute_update(
            agent_id=agent_id,
            agent_path=agent_path,
            request=actual_request,
            target=t,
            intent=intent,
            rules=growth_rules,
            manager=manager,
            classification=classification
        )
        results.append(result)

    return _format_response(actual_request, results, classification)
```

**Add redirect response formatter** (new function):

```python
def _format_rag_redirect(request: str, classification: dict, targets: list[str]) -> str:
    """Format response for requests redirected to RAG knowledge system.

    Instead of writing to files, guides the agent to use the experience() tool.
    """
    truncated = request[:80] + ('...' if len(request) > 80 else '')
    class_type = classification.get("type", "unknown")

    lines = [
        f"📋 Redirected to Knowledge System: \"{truncated}\"",
        f"  Classification: {class_type} ({classification.get('description', '')})",
        f"  Original targets: {', '.join(targets)} → now handled by RAG",
        "",
        "This knowledge is better stored in the RAG knowledge base where it can be",
        "queried and cross-referenced with other project knowledge.",
        "",
        "→ Use the `experience()` tool to record this knowledge:",
        f'  experience(text="{request.replace(chr(34), chr(92)+chr(34))}")',
        "",
        "→ Use the `explore()` tool to query existing knowledge.",
    ]

    return "\n".join(lines)
```

**Also update `_format_rejection()` for project_knowledge** — Since project_knowledge is no longer rejected but redirected:

Remove the `_format_rejection()` function or simplify it, since project_knowledge now goes through `_should_redirect_to_rag()` → `_format_rag_redirect()` instead. The rejection path at line 221-222 is no longer needed:

```python
# REMOVE this block (lines 221-222):
# if classification["type"] == "project_knowledge":
#     return _format_rejection(actual_request, classification)
```

Project knowledge now flows through the normal classification → target resolution → redirect path like all other knowledge types.

#### Edge Cases Handled

| Scenario | Classification | Targets | Action |
|----------|---------------|---------|--------|
| "I learned that early testing catches bugs" | `knowledge` | `["memory", "memories"]` | ✅ Redirect to experience() |
| "My name is Cody" | `identity` | `["soul"]` | ❌ Process normally → soul.md |
| "Be more friendly" | `personality` | `["soul", "user"]` | ❌ Process normally → soul.md + user.md |
| "Always check tests before commit" | `workflow` | `["workflow"]` | ❌ Process normally → workflow.md |
| "User likes TypeScript" | `user_preference` | `["user"]` | ❌ Process normally → user.md |
| "Pattern: always when we use k8s..." | `pattern` | `["memories"]` | ✅ Redirect to experience() |
| "Today we discussed the API design" | `event` | `["memories"]` | ✅ Redirect to experience() |
| "I made a mistake with the SQL query" | `mistake` | `["memories"]` | ✅ Redirect to experience() |
| "I can now do Docker deployments" | `skill` | `["memories"]` | ✅ Redirect to experience() |
| "The project uses postgresql://..." | `project_knowledge` | `["REJECT"]` | ✅ Redirect to experience() (was rejected, now accepted) |
| `intent="remember"` no explicit target | (any) | `["memories"]` | ✅ Redirect to experience() |
| `intent="learn"` no explicit target | (any) | `["memories", "memory"]` | ✅ Redirect to experience() |
| `intent="change", target="workflow"` | `workflow` | `["workflow"]` | ❌ Process normally → workflow.md |
| `intent="change", target="memory"` | (any) | `["memory"]` | ✅ Redirect to experience() |
| Mixed targets (personality) | `personality` | `["soul", "user"]` | ❌ Process normally (not ALL RAG targets) |

### Task 6.2: Deprecate access_memory Tool

**File**: `daemon/tools/access_memory.py` (MODIFIED)

Add deprecation notice — tool still works but warns:
```python
@register_tool_category("self")
@tool
async def access_memory(filename: str) -> str:
    """⚠️ DEPRECATED: Use explore() instead.
    
    This tool is deprecated. Use the explore() tool to query project knowledge.
    """
    return (
        "⚠️ DEPRECATED: access_memory is deprecated. "
        "Use explore() to query project knowledge and experience() to record new knowledge. "
        "See the migration guide for details."
    )
```

### Task 6.3: Update Agent Markdown Files

**Files**: `agents/*/{memory,knowledge,workflow}.md` (per-agent, excluding soul.md)

> ⚠️ **IMPORTANT**: Do NOT modify `soul.md` files. Agent core memory (`agents/*/soul.md`, `agents/*/memory.md`, `agents/*/memories/`) is PART OF THE AGENT DEFINITION and must remain unchanged. Only update project-level files (`.agents/`).

For each agent, update or create:

1. **knowledge.md** (new or update existing):
```markdown
## Knowledge Access

Use `explore(query)` to query project knowledge and `experience(text)` to record new knowledge.

## Project Experience (`.agents/`)

Project-specific knowledge is stored in the working directory's `.agents/` folder:
- `.agents/{agent-id}/memories/` — agent's project experiences
- `.agents/shared/context.md` — project state and goals
- `.agents/shared/conventions.md` — project conventions

## Migration from File-Based Memory

The old file-based memory system (`.agents/{agent-id}/memories/`) is being migrated to the RAG knowledge base.
Use `explore()` and `experience()` instead.

## Agent Core Memory (DO NOT TOUCH)

Agent core memory files are PART OF THE AGENT DEFINITION:
- `agents/{agent}/soul.md` — agent identity and personality
- `agents/{agent}/memory.md` — agent-specific behavioral knowledge
- `agents/{agent}/rule.md` — agent rules and guidelines
- `agents/{agent}/workflow.md` — agent workflow patterns

These are separate from project-level `.agents/` and should NOT be modified.
```

2. **Update memory.md** — Add deprecation notice at top (project-level memory reference only)
3. **Update workflow.md** — Replace file-based memory steps with explore/experience (if applicable)

### Task 6.4: Update meta.json Files

**Files**: `agents/*/meta.json` (MODIFIED — all agents)

Add `"knowledge"` to each agent's tool `allow` list:

```json
{
  "tools": {
    "allow": ["bash", "filesystem", "time", "self", "help", "knowledge"]
  }
}
```

**Agents to update**:
- `agents/coder/meta.json` — add "knowledge"
- `agents/leader/meta.json` — add "knowledge"
- `agents/planner/meta.json` — add "knowledge"
- `agents/reviewer/meta.json` — add "knowledge"
- `agents/tester/meta.json` — add "knowledge"
- `agents/approver/meta.json` — add "knowledge"
- `agents/jober/meta.json` — add "knowledge"
- `agents/giter/meta.json` — add "knowledge"
- `agents/tidier/meta.json` — add "knowledge"
- `agents/_mother/meta.json` — add "knowledge"
- `agents/_baby_template/meta.json` — add "knowledge"

### Task 6.5: Create Migration Guide

**File**: `docs/migration/v1-knowledge-migration.md` (NEW)

Contents:
1. What changed — overview of RAG knowledge system
2. Migration steps for each agent
3. Tool comparison table (old vs new)
4. inner_soul redirect behavior — which classifications redirect, which don't
5. Backward compatibility notes
6. Rollback instructions
7. Troubleshooting

### Task 6.6: Create Migration Script

**File**: `scripts/migrate_memory_to_rag.py` (NEW)

Optional script that:
1. Reads all `.agents/{agent}/memories/*.md` files
2. For each memory file, calls `experience()` with the content
3. Tags each memory with source agent and original date
4. Reports progress and errors
5. Does NOT delete original files (manual cleanup after verification)

```bash
# Usage
python scripts/migrate_memory_to_rag.py --all
python scripts/migrate_memory_to_rag.py --agent coder
python scripts/migrate_memory_to_rag.py --dry-run --all
```

### Task 6.7: Update Configuration Documentation

**File**: `docs/configuration/rag-configuration.md` (NEW)

Document:
1. Environment variables (LIGHTRAG_HOST, LIGHTRAG_API_KEY, LIGHTRAG_WORKSPACE, LIGHTRAG_TIMEOUT)
2. Example configuration
3. Health check endpoint
4. Graceful degradation behavior
5. LightRAG server setup instructions

## Key Files

- `daemon/tools/inner_soul.py` — **MODIFIED**: Add `_should_redirect_to_rag()`, `_format_rag_redirect()`, modify classification flow in `create_inner_soul_tool()`, remove `_format_rejection()` (project_knowledge now accepted)
- `daemon/tools/access_memory.py` — **MODIFIED**: Deprecation notice
- `agents/*/meta.json` — **MODIFIED**: Add "knowledge" to all allow lists
- `agents/*/knowledge.md` — **NEW/MODIFIED**: Knowledge access docs per agent
- `agents/*/memory.md` — **MODIFIED**: Add deprecation notice (project-level memory reference only)
- `agents/*/workflow.md` — **MODIFIED**: Update to use explore/experience (if referencing project memory)
- `agents/*/soul.md` — **NOT MODIFIED**: Agent core identity stays untouched
- `agents/*/rule.md` — **NOT MODIFIED**: Agent rules stay untouched
- `docs/migration/v1-knowledge-migration.md` — **NEW**: Migration guide
- `scripts/migrate_memory_to_rag.py` — **NEW**: Migration script
- `docs/configuration/rag-configuration.md` — **NEW**: RAG config docs

## Constraints

1. **No data loss** — Migration script does NOT delete original files
2. **Backward compatible** — access_memory returns helpful message, doesn't crash
3. **inner_soul preserved** — Self-modification (soul, user, workflow) works exactly as before
4. **Classification-aware** — Redirect based on semantic classification, not simple parameter checks
5. **Gradual migration** — Agents can be updated one at a time
6. **Rollback possible** — Restore original inner_soul.py and memory.md files from git
7. **project_knowledge now accepted** — Previously rejected classification now redirected to experience()
8. **Agent core memory untouched** — Files in `agents/*/` (soul.md, rule.md, memory.md as agent definition) are NEVER modified. Only project-level `.agents/` storage is replaced.

## Deliverables

- [ ] `daemon/tools/inner_soul.py` — `_should_redirect_to_rag()` + `_format_rag_redirect()` + modified flow
- [ ] `daemon/tools/access_memory.py` — Deprecation notice
- [ ] All agent `meta.json` files updated with "knowledge" tool
- [ ] Agent `knowledge.md` files created/updated to reference explore/experience
- [ ] Agent `workflow.md` files updated (project memory references only, not agent workflow logic)
- [ ] Agent `soul.md` / `rule.md` files — **NOT MODIFIED** (agent core identity untouched)
- [ ] Migration guide created
- [ ] Migration script created and tested
- [ ] Configuration documentation created
- [ ] All existing tests pass
- [ ] inner_soul redirect tests: knowledge/pattern/event/skill/mistake → experience()
- [ ] inner_soul non-redirect tests: identity/personality/workflow/user → file updates (agent core)

## Verification

```bash
# Test inner_soul redirect behavior
python -c "
from daemon.tools.inner_soul import _should_redirect_to_rag, _classify_request

# Should redirect (knowledge)
c = _classify_request('I learned that early testing catches bugs')
print(f'knowledge: redirect={_should_redirect_to_rag(c[\"targets\"], c, False)}')

# Should NOT redirect (identity)
c = _classify_request('My name is Cody')
print(f'identity: redirect={_should_redirect_to_rag(c[\"targets\"], c, False)}')

# Should NOT redirect (workflow)
c = _classify_request('Always check tests before committing')
print(f'workflow: redirect={_should_redirect_to_rag(c[\"targets\"], c, False)}')

# Should redirect (project knowledge — was rejected before)
c = _classify_request('The project uses postgresql://...')
print(f'project_knowledge: redirect={_should_redirect_to_rag([\"REJECT\"], c, False)}')
"

# Verify all agents have knowledge tool
for f in agents/*/meta.json; do
    echo "=== $f ==="
    python -c "import json; d=json.load(open('$f')); print(d.get('tools', {}).get('allow', []))"
done

# Verify access_memory deprecation
python -c "from daemon.tools.access_memory import access_memory; print('OK')"

# Verify migration script
python scripts/migrate_memory_to_rag.py --dry-run --all

# Run full test suite
pytest tests/ -v
```
