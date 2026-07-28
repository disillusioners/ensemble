# Phase 1: ContextMessageBuilder Foundation

## Objective
Create pure, unit-testable builder functions that produce `[SYSTEM CONTEXT: ...]` tagged HumanMessages for all 3 context kinds. No integration with existing system — standalone foundation that Phase 3 wires into `agent_node`.

## Coupling
- **Depends on**: None
- **Coupling type**: independent (new module, no shared files)
- **Shared files with other phases**: None (new file only)
- **Shared APIs/interfaces**: `get_shared_context()` from `context_injection.py` (read-only consumer)
- **Why this coupling**: Foundation phase creates the building blocks all other phases depend on

## Context
- No previous phase (root phase)
- Key decision: All builders return `HumanMessage` with `additional_kwargs={"injected_message": True, "context_kind": "<kind>"}` (per ADR-5)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create module skeleton | Create `daemon/services/context_messages.py` with module constants: `CONTEXT_PREFIX`, `CONTEXT_SUFFIX`, `CONTEXT_KINDS` enum (per S1) | `daemon/services/context_messages.py` (new) |
| 2 | Implement `_make_context_message()` helper | Single factory: applies prefix, wraps in HumanMessage with additional_kwargs. Forces consistency across all 3 kinds. | `daemon/services/context_messages.py` |
| 3 | Implement `build_project_context_message()` | Wraps project JSON + shared context metadata KV + critical notes + recent history. **Merges** `format_project_context()` output + `append_shared_context_metadata()` KV into ONE message (per ADR-11). | `daemon/services/context_messages.py`, reads `daemon/manager.py:241-314` |
| 4 | Implement `build_shared_context_message()` | RAG-matched `.md` files via `get_shared_context()`. Drops `<injected_project_context>` XML fence (per ADR-7). Uses markdown formatting. | `daemon/services/context_messages.py`, consumes `daemon/services/context_injection.py:743-859` |
| 5 | Implement `build_skills_message()` | Unified skill injection: wraps `inject_skills()` / `inject_explicit_skill()` output. Changes prefix from `[System Inject]` to `[SYSTEM CONTEXT: Skills]`. | `daemon/services/context_messages.py`, wraps `skill_injection_service.py:573-711` |
| 6 | Add `escape_for_context_block()` helper | Port escaping logic from `_format_shared_context_kv_block` (`&`/`<`/`>` → unicode escapes). Apply to untrusted content. Markdown code fences for embedded blocks. (per ADR-7) | `daemon/services/context_messages.py` |
| 7 | Implement `assemble_context_messages()` orchestrator | Async entry point returning `[project_msg?, shared_context_msg?, skills_msg?]` in canonical order. Handles opt-in flags (context_injection, skill_injection). Returns empty list if all disabled. | `daemon/services/context_messages.py` |
| 8 | Export from `__init__.py` | Add `assemble_context_messages`, `build_*` functions to `daemon/services/__init__.py` exports | `daemon/services/__init__.py` |
| 9 | Write unit tests | Test all 3 builders: prefix format, additional_kwargs flags, content structure, escaping, edge cases (empty context, missing project). Mock DB + RAG. | `tests/unit/test_context_messages.py` (new) |

## Key Files
- `daemon/services/context_messages.py` — NEW: all builder functions (primary deliverable)
- `daemon/services/context_injection.py` — EXISTING: `get_shared_context()` consumed read-only
- `daemon/manager.py` — EXISTING: `format_project_context()` logic to be ported (lines 241-314)
- `daemon/services/skill_injection_service.py` — EXISTING: `_format_injection()` logic to wrap (lines 573-711)
- `daemon/services/__init__.py` — EXISTING: add exports
- `tests/unit/test_context_messages.py` — NEW: unit tests

## Constraints
- Builders must be testable in isolation (mockable DB/RAG)
- Must handle `get_shared_context()` returning empty string gracefully
- Must preserve existing escaping security (`_format_shared_context_kv_block` pattern)
- All messages must have `additional_kwargs={"injected_message": True, "context_kind": "<kind>"}`
- `assemble_context_messages()` should be async-ready (Phase 3 calls it from async `agent_node`)
- **Opencode path is OUT OF SCOPE (per ADR-13)**: These builders are for the ensemble `agent_node` path only. They must NOT be wired into `external_opencode_send_message` or the opencode tool's `related_context_keywords` mechanism.

## Implementation Details

### Context kind enum (per S1):
```python
CONTEXT_KIND_PROJECT = "project"
CONTEXT_KIND_SHARED_CONTEXT = "shared_context"
CONTEXT_KIND_SKILLS = "skills"
```

### `_make_context_message()` signature:
```python
def _make_context_message(kind: str, title: str, content: str) -> HumanMessage:
    return HumanMessage(
        content=f"[SYSTEM CONTEXT: {title}]\n\n{content}",
        id=str(uuid.uuid4()),
        additional_kwargs={"injected_message": True, "context_kind": kind},
    )
```

### `assemble_context_messages()` signature (async):
```python
async def assemble_context_messages(
    instance_id: str,
    user_query: str,
    project_id: str | None,
    agent_meta: Any,
    manager: Any,
    instance_repository: Any,
    parent_id: str | None = None,
    skill_injection_result: tuple[str, list[str]] | None = None,  # pre-computed (text, skill_ids), or None → builder searches
) -> list[HumanMessage]:
```

**Note**: `skill_injection_result` is optional. If pre-computed by the messaging path (stored via `manager.set_context_skill_result()`), it's passed in. If `None` (e.g., retry without first attempt having run), the builder runs the skill search itself (B3 fix).

## Deliverables
- [ ] `daemon/services/context_messages.py` created with all 3 builders + orchestrator
- [ ] All builders produce correct `[SYSTEM CONTEXT: ...]` prefix
- [ ] All messages have `additional_kwargs` with `injected_message` and `context_kind`
- [ ] Unit tests pass for all builders
- [ ] Exported from `__init__.py`
