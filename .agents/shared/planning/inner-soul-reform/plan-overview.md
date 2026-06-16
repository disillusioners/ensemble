# Plan Overview: Inner Soul Reform

## Objective
Reform the `inner_soul` tool to prevent misuse: stop it from accepting project state content (git operations, task progress, deployment info) and instead reject with helpful hints pointing to `project_history_add()` and `experience()`. Add clear decision-table guidance to agent prompts.

## Scope Assessment
**MEDIUM** — 3 components across backend code, agent prompt files, and tests. The core file (`inner_soul.py`) is 1176 lines and has an established test suite (16+ tests referencing `knowledge` category will need updates). Changes are surgical (classification rules, description text, rejection handler) but require careful regression testing.

## Context
- Project: agents-ensemble
- Working Directory: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`

## Critical Constraint: RAG Dependency (F2)

**RAG is NOT enabled by default.** The `is_rag_enabled()` function (`daemon/rag/config.py:81-88`) returns `True` only when:
1. Module-level `_rag_enabled` flag is `True` (default: `True`, line 13), AND
2. `LIGHTRAG_HOST` environment variable is set (`RAGConfig.from_env().is_configured`, line 88)

If `LIGHTRAG_HOST` is not set, RAG is disabled. In this state:
- `_should_redirect_to_rag()` returns `False` for everything (line 219 guard)
- Knowledge-oriented classifications (`knowledge`, `pattern`, `event`, `skill`, `mistake`) fall through to file writes
- `project_knowledge` → `_execute_update("REJECT")` → "Unknown target: REJECT" error

**This means the pre-classification rejection is the PRIMARY defense when RAG is disabled.** When RAG is enabled, the RAG redirect already catches most knowledge-oriented content — but the pre-classification rejection is still valuable as an explicit guard for `project_knowledge` patterns and provides clearer messaging.

**The reform must work correctly in BOTH states:**
- RAG enabled: project_knowledge patterns → RAG redirect to `experience()` (unchanged for existing patterns, new patterns also benefit)
- RAG disabled: project_knowledge patterns → graceful `_format_project_rejection()` with tool hints (NEW behavior, replaces broken "Unknown target: REJECT")

## Root Cause Analysis (from exploration)

The leak has **three layers**, each requiring a fix:

### Layer 1: Tool Description Too Vague
- Line 516: `"""Remember, learn, or change yourself."""` — no warning about what NOT to use it for
- CATEGORY_DOC (lines 29-34): `Remember, learn, or change agent behavior and access memories.` — too broad
- `_full_doc_` (lines 627-674): Describes `memory.md` as "What you KNOW" and `memories/` as "What happened" — invites project content

### Layer 2: Classification Accepts Project Content
- The `knowledge` category (lines 89-101) has patterns like `\bremember that\b`, `\bi learned that\b` — these match BOTH persona and project content.
- The `project_knowledge` category (lines 159-181) has 25+ patterns but only catches specific tech terms (docker, postgres, k8s, package.json, etc.). It MISSES: git operations, branch names, task progress, "setup complete" statements, generic code changes.
- The **fallback** (lines 790-797): anything not matching → `event`/`memories`. So "Git setup complete. Branch: feature/db-tools created from latest" matches NOTHING and falls to the event fallback.
- **When RAG is enabled**: `event`/`knowledge`/`pattern`/`skill`/`mistake` classifications → `_should_redirect_to_rag()` returns True → redirected to `experience()`. This works but only for RAG-configured deployments.
- **When RAG is disabled**: the fallback writes project content directly to `memories/*.md` files. This is the worst case — and the default state when `LIGHTRAG_HOST` is not set.
- The `REJECT` target has **no graceful handler**: `_execute_update()` (line 822-823) returns generic `{"success": False, "error": f"Unknown target: {target}"}`.

### Layer 3: No Prompt Guidance
- **Zero guidance** about `inner_soul` exists in any leader file (soul.md, workflow.md, rule.md, tools_note.md, memory.md).
- The shared `agents/_prompt_system/knowledge.md` (injected as section 10 of all agent prompts) documents `explore()` and `experience()` but NOT `inner_soul`.
- The only recording guidance is `project_history_add()` in `rule.md:127-128`.
- Agents have no decision table to distinguish the tools.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backend Classification Reform | Rework tool description, classification rules (compound patterns + persona exemptions), and rejection logic in `inner_soul.py` | None | — (root) | 4-5h |
| 2 | Agent Prompt Decision Table | Add recording/remembering decision table to leader rule.md and shared knowledge.md | None | independent | 1-2h |
| 3 | Test Coverage | Add rejection tests, persona preservation tests, update 16+ breaking existing tests | Phase 1 | tight | 3-4h |

### Coupling Assessment

| Phase Pair | Coupling | Reasoning |
|------------|----------|-----------|
| 1 ↔ 2 | **independent** | Phase 1 touches `daemon/tools/inner_soul.py`; Phase 2 touches `agents/` markdown files. No shared files, no API dependencies. Can run in parallel. |
| 1 ↔ 3 | **tight** | Phase 3 tests the exact classification rules and rejection messages defined in Phase 1. Must wait for Phase 1 review. |
| 2 ↔ 3 | **independent** | Phase 2 is prompt text; Phase 3 is backend tests. No dependency. |

**Recommended schedule:** Phase 1 and Phase 2 in parallel → Phase 3 after Phase 1.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Expanded `project_knowledge` patterns over-match legitimate persona content (F1) | **critical** | Use **compound action-context patterns** (verb+noun combos) instead of single-word patterns. Add **persona-intent exemption**: if statement starts with "I should" / "I am" / "I learned" / "My approach" / "I tend to", skip project-rejection. 20+ persona test cases required. |
| Classification order matters — `project_knowledge` must be checked BEFORE `knowledge` and `event` fallback | high | Pre-classification heuristic with persona exemption runs first, then project patterns, then normal classification |
| `knowledge` category removal breaks 16+ existing tests (F5) | medium | Enumerate ALL breaking tests (see Phase 3 Task 3). Each gets explicit update note. |
| Compound requests bypass per-part rejection (F4) | high | Pre-classification runs per-part inside the compound-request branch (lines 580-606). Test with mixed persona+project compound input. |
| Two variants of knowledge.md (with/without force_explore) must stay in sync | low | Add same decision table to both files; use a shared note |
| RAG disabled = no protection (F2) | high | Pre-classification rejection works regardless of RAG state. Explicitly documented as critical constraint. |

## Success Criteria
- [ ] `inner_soul` tool description explicitly says it's for persona/behavioral reflection, NOT project state
- [ ] Content like "Git setup complete. Branch: feature/db-tools" is REJECTED with a helpful message pointing to correct tools
- [ ] Content like "Be more concise in responses" still works (persona change accepted)
- [ ] Content like "User prefers TypeScript" still works (user preference accepted)
- [ ] **Compound patterns**: "completed a build" is rejected but "my approach to building" is NOT (F1 fix)
- [ ] **Persona exemptions**: "I should be more methodical in my task approach" → accepted (NOT rejected despite "task")
- [ ] **Compound requests**: "Be more concise AND I deployed the new build to k8s" → part 1 accepted, part 2 rejected (F4 fix)
- [ ] Leader prompt has a decision table distinguishing inner_soul, project_history_add, and experience
- [ ] All 16+ breaking tests updated and passing
- [ ] New tests cover: project content rejection, persona acceptance (20+ cases), graceful REJECT handler, compound per-part rejection, helpful hint message
- [ ] Works correctly in BOTH RAG-enabled and RAG-disabled states

## Deferred Items (noted for follow-up, do not block implementation)
- **F6**: Migration of historical leaked content in `.agents/*/memories/*.md` — track as follow-up task after reform is deployed
- **F7-F9**: Smoke test, RAG config verification in dev env, soul.md cross-reference — part of integration verification, not blocking

## Tracking
- Created: 2025-06-15
- Last Updated: 2025-06-15 (Revision 2: F1, F2, F4, F5 fixes)
- Status: draft
