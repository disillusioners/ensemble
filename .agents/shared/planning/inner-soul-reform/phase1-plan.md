# Phase 1: Backend Classification Reform

## Objective
Rework the `inner_soul` tool's description, classification rules, and rejection logic in `daemon/tools/inner_soul.py` so that project-related content is actively rejected with helpful hints — while preserving all legitimate persona/behavioral self-reflection use cases.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/tools/inner_soul.py` (Phase 3 tests this file's logic)
- **Shared APIs/interfaces**: `_classify_request()`, `_should_redirect_to_rag()`, `_format_rag_redirect()`, CLASSIFICATION_RULES
- **Why this coupling**: Phase 3 tests depend on the exact classification behavior and rejection messages defined here

## Context
- Previous phase: None
- Key architectural decisions:
  - Classification is regex-based (no LLM fallback — deferred to "Phase 5" per code comment at line 778-780)
  - **RAG is NOT enabled by default** — requires `LIGHTRAG_HOST` env var (see plan-overview F2 constraint). The pre-classification rejection is the PRIMARY defense when RAG is disabled.
  - When RAG is enabled, knowledge-oriented classifications redirect to `experience()` — this is desirable behavior
  - When RAG is disabled, knowledge-oriented classifications fall through to file writes — this is where leaks happen
  - The `project_knowledge` category has 25+ patterns but misses common project content (git ops, task progress, generic code changes)
  - The `REJECT` target has no graceful handler in `_execute_update()` — it produces a generic "Unknown target" error

---

## Tasks

### Task 1: Fix Tool Description & CATEGORY_DOC
**File**: `daemon/tools/inner_soul.py`

| Item | Current (Line) | New |
|------|----------------|-----|
| Tool docstring | `"""Remember, learn, or change yourself. Use tool_help("inner_soul") for details."""` (line 516) | `"""Reflect on your persona, change behavioral patterns, or remember user interaction preferences. NOT for project state, task progress, code, or working logs. Use tool_help("inner_soul") for details."""` |
| CATEGORY_DOC | `Remember, learn, or change agent behavior and access memories.` (line 30) | `Reflect on your own persona, behavioral patterns, and user interaction preferences. NOT for project state, task progress, code, or working logs.` |
| `_full_doc_` intro | `The core of agent growth - remember, learn, and evolve.` (line 627) | Keep as-is but add a prominent warning section (see below) |
| `_full_doc_` file purposes | `memory.md - What you KNOW` and `memories/ - What happened` (lines 636-637) | Change to `memory.md - Persona insights (NOT project knowledge)` and `memories/ - Personal observations (NOT task logs)` |

**Add to `_full_doc_`** (after the intro, before "## Files and Their Purposes"):
```
## ⚠️ What This Tool is NOT For

This tool is **INTENSELY PERSONAL** — it's about YOU (the agent) and the USER as personas.

**DO NOT use inner_soul for:**
- Project events or task progress → use `project_history_add()`
- Project knowledge, architecture, code insights → use `experience()`
- Git operations, branch names, deployment status → use `project_history_add()`
- Anything about code, configs, infrastructure, or the project itself → use `experience()`

**This tool WILL REJECT project-related content.**

If your content mentions branches, commits, deployments, code, files, configs,
databases, APIs, or project tasks, it will be rejected with guidance on which
tool to use instead.
```

**Remove `memory` and `memories` from target types in docstring** (lines 649):
Currently: `target: Literal["memory", "workflow", "soul", "user", "memories"]`
Keep the Literal for backward compatibility but in the doc text, clarify that memory/memories targets are deprecated.

---

### Task 2: Expand `project_knowledge` Patterns — Compound Action-Context Patterns (F1 FIX)

**File**: `daemon/tools/inner_soul.py`, lines 159-181

**⚠️ CRITICAL DESIGN PRINCIPLE (F1)**: Do NOT use single-word patterns like `\btask\b`, `\bbuild\b`, `\bdeploy\b`. These will false-positive on legitimate persona reflections like "I should be more methodical in my **task** approach" or "My approach to **building** solutions should be structured".

**Instead, use compound verb+noun patterns** that require a project ACTION combined with a project OBJECT. These only match when someone is reporting a completed project activity, not reflecting on their behavior.

```python
"project_knowledge": {
    "patterns": [
        # --- EXISTING (keep all 25+ patterns) ---
        # Project-specific paths and structures
        r"\btest/packs?\b", r"\bsrc/\b", r"\bconfig/\b", r"\bdocs/\b",
        r"\btest\s+pack\b", r"\btest\s+script\b", r"\bbash\s+script\b",
        # Specific project/tool names
        r"\bllm-supervisor-proxy\b", r"\bagents-ensemble\b", r"\bmy\s+project\b",
        # Infrastructure and tech stack (keep — these are unambiguous tech terms)
        r"\bpostgresql\b", r"\bpostgres\b", r"\bmysql\b", r"\bsqlite\b",
        r"\bkubernetes\b", r"\bk8s\b", r"\bdocker\b", r"\bterraform\b",
        r"\baws\b", r"\bgcp\b", r"\bazure\b",
        # Project-specific configs
        r"\.env\b", r"\bconfig\.yaml\b", r"\bsettings\.py\b",
        r"\bpackage\.json\b", r"\brequirements\.txt\b", r"\bpyproject\.toml\b",
        # Database/server terminology
        r"\bpostgres(ql)?://\b", r"\bredis://\b", r"\bmongo://\b",
        # Deployment/infrastructure
        r"\bdeployment\b", r"\bci/cd\b", r"\bgithub\s+actions\b",
        r"\bpipeline\b", r"\bhelm\s+chart\b",

        # --- NEW: Git operations (compound: verb + git-noun) ---
        r"\bgit\s+(push|pull|commit|merge|rebase|checkout|clone|init)\b",
        r"\b(created?|merged?|pushed?|pulled?|checked\s?out|cloned?|rebased?)\s+(a|the|new)?\s*(branch|commit|pr|pull\s+request)\b",
        r"\bbranch\s+\S+\s+(created|deleted|merged|from)\b",
        r"\bpull\s+request\s+(created|merged|opened|closed|approved)\b",
        r"\bcommit(ted)?\s+(to|on|in)\s+\S+",

        # --- NEW: Task/work completion (compound: completion-verb + work-noun) ---
        r"\b(completed?|finished?|done\s+with|shipped?)\s+(a|the|my)?\s*(task|feature|build|deploy|deploy?ment|release|sprint|milestone)\b",
        r"\bsetup\s+(complete|done|finished)\b",

        # --- NEW: Code changes (compound: change-verb + code-noun) ---
        r"\b(refactored?|rewrote?|updated?|modified?|fixed|patched?|added?|removed?|deleted?)\s+(the|a|my)?\s*(code|api|endpoint|route|schema|database|table|model|function|class|method|component|service|controller|handler)\b",
        r"\bbug\s*(fix|fixed|fixing)\b",
        r"\b(code|api|database|schema)\s+(change|update|fix|refactor|migration)\b",
        r"\bcreated?\s+(a|the|new)\s+\w+\.(py|js|ts|tsx|jsx|md|json|yaml|yml|toml|sql|go|rs|java)\b",

        # --- NEW: Deployment status (compound: deploy-verb + target) ---
        r"\bdeployed?\s+(to|on|in)\s+\S+",
        r"\bdeployed?\s+(a|the|new)\s+\S+",
        r"\bbuilt?\s+(and\s+)?deployed?\b",
    ],
    "targets": ["REJECT"],
    "description": "Project-specific knowledge - must NOT enter agent memory"
}
```

**Why compound patterns solve F1**:

| Input | Single-word pattern (BAD) | Compound pattern (GOOD) |
|-------|--------------------------|------------------------|
| "I should be more methodical in my task approach" | `\btask\b` → REJECT ❌ | No compound match → ACCEPT ✅ |
| "My approach to building solutions should be structured" | `\bbuild(ing)?\b` → REJECT ❌ | No compound match → ACCEPT ✅ |
| "I learned that endpoint design matters" | `\bendpoint\b` → REJECT ❌ | No compound match → ACCEPT ✅ |
| "completed a build" | (missed) | `\bcompleted?\s+(a|the|my)?\s*...build\b` → REJECT ✅ |
| "merged a branch called feature/auth" | (missed) | `\bmerged?\s+(a|the|new)?\s*branch\b` → REJECT ✅ |
| "deployed to production" | (missed) | `\bdeployed?\s+to\s+\S+` → REJECT ✅ |

---

### Task 3: Add Persona-Intent Exemption Layer (F1 FIX — Defense in Depth)

**File**: `daemon/tools/inner_soul.py`

Even with compound patterns, there's residual risk of false positives. Add a **persona-intent exemption** that runs BEFORE the project_knowledge check: if the statement starts with a self-reflection prefix, skip project-rejection entirely.

```python
# Persona-intent prefixes that indicate self-reflection, not project reporting
_PERSONA_INTENT_PREFIXES = [
    r"^\s*i\s+(should|need to|must|ought to|want to|tend to|always|never|usually)\b",
    r"^\s*i\s+(am|'m)\s+(a|an|the)?\s*\w*",  # "I am a DevOps agent"
    r"^\s*i\s+(learned|realized|discovered)\s+that\s+(i|my|being|early|the\s+user)",  # persona learning
    r"^\s*my\s+(approach|style|tone|voice|strategy|tendency|habit|philosophy|strength|weakness)\b",
    r"^\s*i\s+(value|believe|care\s+about|strive\s+to|aim\s+to)\b",
    r"^\s*be\s+(more|less|cozy|warm|cold|formal|casual|concise|verbose)\b",
    r"^\s*user\s+(likes|prefers|wants|needs|always|never)\b",
    r"^\s*remember\s+(my|your|the\s+user)"  # "remember my name" not "remember that docker..."
]
```

**Integration**: This is checked FIRST in `_classify_request()` (see Task 5). If any persona prefix matches, skip the project_knowledge pre-check entirely and proceed with normal semantic classification.

**Key distinction**:
- `"I learned that early testing catches bugs"` → matches `^\s*i\s+(learned|realized)...` → persona exemption → accepted ✅
- `"I learned that the API uses REST"` → does NOT match persona prefix (`the API` after "learned that" ≠ persona words) → project check → `\brefactored?\s+...api\b` or similar → REJECTED ✅
- `"I am a DevOps specialist"` → matches `^\s*i\s+(am|'m)\s+(a|an)?` → persona exemption → accepted ✅

---

### Task 4: Add Graceful REJECT Handler
**File**: `daemon/tools/inner_soul.py`

Currently when RAG is disabled and classification is `project_knowledge`:
- `_should_redirect_to_rag()` returns False (line 219)
- `_execute_update()` with target `"REJECT"` → generic `"Unknown target: REJECT"` error (line 822-823)

**Fix**: Add explicit REJECT handling in the main `inner_soul()` function. This needs to be applied in **TWO places** — the single-request branch (line 554) AND the compound-request per-part branch (line 587) (F4 FIX):

**Single-request branch** (after line 554, before `_execute_update` calls):
```python
# After _should_redirect_to_rag() check:
if "REJECT" in targets:
    return _format_project_rejection(actual_request, classification)
```

**Compound-request branch** (after line 587, inside the `for idx, part in enumerate(...)` loop):
```python
# Check for explicit rejection per-part (F4: per-part rejection in compound requests)
if "REJECT" in targets:
    rejection_msg = _format_project_rejection(part, classification)
    compound_lines.append(f"  Part {idx}: \"{part[:50]}...\" → project_knowledge (REJECTED)")
    compound_lines.append(f"    {rejection_msg.split(chr(10))[0]}")
    all_results.append({"part": part, "rejected": True, "response": rejection_msg})
    continue
```

**New function** `_format_project_rejection()` (add near `_format_rag_redirect` around line 242):

```python
def _format_project_rejection(request: str, classification: dict) -> str:
    """Format rejection message for project-related content.

    When content is classified as project_knowledge and RAG is disabled,
    we can't redirect to experience() — but we still reject with guidance.
    """
    truncated = request[:80] + ('...' if len(request) > 80 else '')
    lines = [
        f"⛔ Rejected: \"{truncated}\"",
        f"  Classification: project_knowledge ({classification.get('description', '')})",
        "",
        "This looks project-related. inner_soul is for persona/behavioral",
        "reflection ONLY — not project state, task progress, or code.",
        "",
        "→ For project events (features, fixes, deployments):",
        "  project_history_add(project_id=..., entry_type='feature', summary='...')",
        "",
        "→ For project knowledge (architecture, patterns, gotchas):",
        "  experience(text='...')",
        "",
        "→ inner_soul is ONLY for:",
        "  - Your persona changes ('Be more concise')",
        "  - User preferences ('User prefers TypeScript')",
        "  - Self-reflection ('I learned that early feedback improves outcomes')",
    ]
    return "\n".join(lines)
```

---

### Task 5: Pre-Classification Heuristic with Persona Exemption (F1 + F4 FIX)

**File**: `daemon/tools/inner_soul.py`, function `_classify_request()` (line 740)

This is the core algorithmic fix. Replace the existing classification flow with a three-stage approach:

```python
def _classify_request(request: str, intent: str | None = None) -> dict:
    request_lower = request.lower()

    # ========================================
    # STAGE 1: Persona-intent exemption (F1)
    # ========================================
    # If the statement starts with a self-reflection prefix, skip
    # project-rejection entirely. This prevents false positives like
    # "I should be more careful with deployments" from being rejected.
    for prefix_pattern in _PERSONA_INTENT_PREFIXES:
        if re.search(prefix_pattern, request_lower, re.IGNORECASE):
            # Persona intent detected — skip project_knowledge check,
            # proceed directly to normal semantic classification
            break
    else:
        # ========================================
        # STAGE 2: Project-content pre-check (only if NOT persona intent)
        # ========================================
        # Check project_knowledge patterns FIRST, before any other classification.
        # This ensures project content is never accepted into agent memory.
        project_patterns = CLASSIFICATION_RULES["project_knowledge"]["patterns"]
        for pattern in project_patterns:
            if re.search(pattern, request_lower, re.IGNORECASE):
                return {
                    "type": "project_knowledge",
                    "targets": ["REJECT"],
                    "description": CLASSIFICATION_RULES["project_knowledge"]["description"],
                    "pattern_matched": pattern,
                    "all_matches": ["project_knowledge"],
                }

    # ========================================
    # STAGE 3: Normal semantic classification (existing logic)
    # ========================================
    # Check each non-project classification type
    matches = []
    for class_type, rules in CLASSIFICATION_RULES.items():
        if class_type == "project_knowledge":
            continue  # Already handled in Stage 2
        for pattern in rules["patterns"]:
            if re.search(pattern, request_lower, re.IGNORECASE):
                matches.append({
                    "type": class_type,
                    "targets": rules["targets"],
                    "description": rules["description"],
                    "pattern_matched": pattern
                })
                break

    if matches:
        # Merge all unique targets
        all_targets = []
        for m in matches:
            for t in m["targets"]:
                if t not in all_targets:
                    all_targets.append(t)

        best = matches[0]
        best["targets"] = all_targets
        best["all_matches"] = [m["type"] for m in matches]
        return best

    # Fallback (unchanged)
    if intent == "remember":
        return {"type": "event", "targets": ["memories"],
                "description": "Event or observation (remember intent fallback)",
                "all_matches": []}

    return {"type": "event", "targets": ["memories"],
            "description": "Event or observation", "all_matches": []}
```

**Classification flow summary**:
```
Input → Persona prefix match?
  YES → Skip project check → Normal classification (identity/personality/etc.)
  NO  → Project pattern match?
    YES → REJECT (project_knowledge)
    NO  → Normal classification → fallback to event/memories
```

**F4 NOTE**: This function is called PER-PART in the compound-request branch (line 581). The persona exemption and project pre-check therefore apply automatically to each part of a compound request. No additional code needed beyond ensuring `_classify_request()` is called per-part (which it already is at line 581).

---

### Task 6: Narrow `knowledge` Category Patterns
**File**: `daemon/tools/inner_soul.py`, lines 89-101

**Decision**: **Remove `knowledge` category entirely**. Its targets (`memory`, `memories`) are now fully handled by the RAG system via redirect when RAG is enabled, and the project-content pre-check prevents project leaks when RAG is disabled.

Persona-relevant "I learned that X" statements will route through:
- Persona exemption → if "I learned that **I**..." → accepted as persona
- `mistake` category → if "Mistake:..." or "Lesson learned:..." → RAG redirect
- `pattern` category → if "I noticed that..." → RAG redirect
- `skill` category → if "I can now..." or "New skill:..." → RAG redirect
- `event` fallback → for other statements → RAG redirect (when RAG enabled)

**Changes required**:
1. Remove the `knowledge` entry from `CLASSIFICATION_RULES` (lines 89-101)
2. Remove `"knowledge"` from `_KNOWLEDGE_CLASSIFICATIONS` set (line 188-190)
3. Update all 16+ breaking tests (see Phase 3 Task 3 for full enumeration)

---

## Key Files
- `daemon/tools/inner_soul.py` — Core tool (1176 lines): description, classification, rejection
- `daemon/tools/_tool_registry.py` — Tool registration (reads description from docstring)
- `daemon/loader.py` — Prompt composition (uses CATEGORY_DOC from inner_soul.py)

## Constraints
- Must preserve all legitimate self-modification use cases (identity, personality, user prefs, workflow)
- Must not break the RAG redirect mechanism (it's working correctly for knowledge→experience)
- Regex patterns can't catch everything — document the limitation
- **Compound patterns (F1)**: NEVER use single-word patterns for task/build/deploy/endpoint — always require verb+noun combination
- **Persona exemption (F1)**: Self-reflection prefixes skip project-rejection entirely
- **Compound requests (F4)**: Rejection logic runs per-part, inside the compound-request loop
- Don't change the tool's function signature (backward compatibility)
- **RAG dependency (F2)**: Must work correctly when RAG is disabled — pre-classification is the only protection

## Deliverables
- [ ] Updated tool description (docstring + CATEGORY_DOC + _full_doc_) with clear "NOT for project" warnings
- [ ] Expanded `project_knowledge` patterns using **compound verb+noun patterns** (no single-word patterns)
- [ ] `_PERSONA_INTENT_PREFIXES` list and persona exemption logic in `_classify_request()`
- [ ] Pre-classification heuristic with persona exemption (Stage 1 → Stage 2 → Stage 3)
- [ ] Graceful `_format_project_rejection()` handler, applied in BOTH single-request and compound-request branches
- [ ] Removed `knowledge` category from CLASSIFICATION_RULES and `_KNOWLEDGE_CLASSIFICATIONS`
- [ ] No regressions in identity/personality/user/workflow classification
