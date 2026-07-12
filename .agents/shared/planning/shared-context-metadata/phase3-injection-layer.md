# Phase 3: Injection Layer

## Objective

Create `append_shared_context_metadata()` function that fetches all KV pairs for a context_key and injects them into the system prompt. Wire it into the post-processing chain at both spawn and restore call sites, positioned after `append_context_key()` and before `append_current_time()`.

## Coupling

- **Depends on**: Phase 1 (Storage Layer)
- **Coupling type**: tight — calls `SharedContextMetadataRepository.get_all_as_dict()` from Phase 1
- **Shared files with other phases**: 
  - `daemon/services/instance_lifecycle.py` — modified at 3 points (function definition + 2 call sites)
- **Shared APIs/interfaces**: Uses `SharedContextMetadataRepository.get_all_as_dict(context_key)`
- **Why this coupling**: Injection needs to read metadata from the DB via the repository

## Context

- **Previous phase completed**: `SharedContextMetadataRepository` with `get_all_as_dict()` method available
- **Current post-processing chain** (in `daemon/services/instance_lifecycle.py`):
  ```
  load_and_cache_prompt()
    → append_context_key(...)           ← line ~560 (spawn) / ~1523 (restore)
    → append_current_time(...)          ← line ~562 (spawn) / ~1525 (restore)
    → append_user_language(...)         ← line ~565 (spawn) / ~1528 (restore)
  ```
- **Insertion point**: Between `append_context_key()` and `append_current_time()` — metadata is logically adjacent to the context key section
- **Both call sites** already have `instance_repository`, `instance_id`, and `parent_id` (or `meta.parent_id`) in scope

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Define `append_shared_context_metadata()` | New function that fetches KV pairs via repository and formats them into a `# Shared Context` section with `---` separator. Graceful degradation on error (skip injection). | `daemon/services/instance_lifecycle.py` (after `append_user_language`, ~line 250) |
| 2 | Add shared_context_repository parameter | The function needs access to the repository. Pass it as a parameter (same as `instance_repository` is passed to `append_context_key`). | `daemon/services/instance_lifecycle.py` |
| 3 | Wire into spawn call site | Insert call between `append_context_key()` and `append_current_time()` at ~line 560. Pass `shared_context_repository` from manager. | `daemon/services/instance_lifecycle.py:~560` |
| 4 | Wire into restore call site | Insert call between `append_context_key()` and `append_current_time()` at ~line 1523. Pass `shared_context_repository` from manager. | `daemon/services/instance_lifecycle.py:~1523` |
| 5 | Resolve repository access in spawn/restore | `spawn_instance` and `_restore_instance` need access to `shared_context_repository`. Check if `manager` is available in scope or pass repository as parameter. | `daemon/services/instance_lifecycle.py` |

## Key Files

### Modified File: `daemon/services/instance_lifecycle.py`

#### New function definition (after `append_user_language`, ~line 250)

```python
def append_shared_context_metadata(
    system_prompt: str,
    context_key: str,
    shared_context_repository: Optional["SharedContextMetadataRepository"] = None,
) -> str:
    """Append shared context metadata KV pairs to the system prompt.

    Fetches all key-value metadata for the given context_key and injects
    them into a "# Shared Context" section. This runs for ALL agent types,
    not just explorer agents.

    If the repository is None or no metadata exists, the prompt is returned
    unchanged (graceful degradation — never blocks agent spawn).

    Args:
        system_prompt: The base system prompt to append to.
        context_key: The context key (tree root instance ID) to fetch metadata for.
        shared_context_repository: Repository for metadata queries.

    Returns:
        The system prompt with metadata section appended, or unchanged if no metadata.
    """
    if shared_context_repository is None:
        return system_prompt

    try:
        kv_pairs = shared_context_repository.get_all_as_dict(context_key)
    except Exception as e:
        logger.warning(
            "append_shared_context_metadata: failed to fetch metadata for "
            "context_key=%s: %s — skipping injection",
            context_key, e,
        )
        return system_prompt

    if not kv_pairs:
        # No metadata — don't inject an empty section
        return system_prompt

    # Format as JSON for clean, structured representation
    import json
    metadata_json = json.dumps(kv_pairs, indent=2, ensure_ascii=False)

    section = (
        f"\n\n# Shared Context\n\n"
        f"context_key: {context_key}\n\n"
        f"## Metadata KV\n\n"
        f"{metadata_json}\n"
        f"\n---\n"
    )
    return system_prompt + section
```

#### Modified spawn call site (~line 555-567)

**Before:**
```python
system_prompt = append_context_key(system_prompt, instance_id, instance_repository, parent_id=parent_id)
system_prompt = append_current_time(system_prompt)
system_prompt = append_user_language(system_prompt, user_language)
```

**After:**
```python
system_prompt = append_context_key(system_prompt, instance_id, instance_repository, parent_id=parent_id)

# Append shared context metadata (KV pairs available to ALL agent types)
# Resolve root_id for context_key (same logic as append_context_key)
if parent_id is None:
    _root_id = instance_id
else:
    _root_id = instance_repository.get_tree_root_id(parent_id)
    if _root_id is None:
        _root_id = parent_id
shared_context_repo = manager.get_shared_context_repository() if hasattr(manager, 'get_shared_context_repository') else None
system_prompt = append_shared_context_metadata(system_prompt, _root_id, shared_context_repo)

system_prompt = append_current_time(system_prompt)
system_prompt = append_user_language(system_prompt, user_language)
```

#### Modified restore call site (~line 1516-1530)

**Before:**
```python
system_prompt = append_context_key(system_prompt, instance_id, instance_repository, parent_id=meta.parent_id)
system_prompt = append_current_time(system_prompt)
system_prompt = append_user_language(system_prompt, user_language)
```

**After:**
```python
system_prompt = append_context_key(system_prompt, instance_id, instance_repository, parent_id=meta.parent_id)

# Append shared context metadata (KV pairs available to ALL agent types)
if meta.parent_id is None:
    _root_id = instance_id
else:
    _root_id = instance_repository.get_tree_root_id(meta.parent_id)
    if _root_id is None:
        _root_id = meta.parent_id
shared_context_repo = manager.get_shared_context_repository() if hasattr(manager, 'get_shared_context_repository') else None
system_prompt = append_shared_context_metadata(system_prompt, _root_id, shared_context_repo)

system_prompt = append_current_time(system_prompt)
system_prompt = append_user_language(system_prompt, user_language)
```

## Injected Format

When metadata exists, the system prompt will contain:

```markdown
## Context Key

CONTEXT_KEY: abc-123-def

# Shared Context

context_key: abc-123-def

## Metadata KV

{
  "project_change_scope": "BIG",
  "decision": "use OAuth2"
}

---

## Current Time

ISO: 2026-07-12T10:40:26+00:00
...
```

When NO metadata exists, the system prompt is unchanged (no empty `# Shared Context` header):

```markdown
## Context Key

CONTEXT_KEY: abc-123-def

## Current Time

ISO: 2026-07-12T10:40:26+00:00
...
```

## Design Decisions

### Why after `append_context_key()` and before `append_current_time()`?

1. **Logical adjacency**: Metadata is scoped by `context_key` — placing it right after the Context Key section makes the relationship clear.
2. **root_id already computed**: `append_context_key()` computes `root_id` internally. We recompute it at the call site (small cost, avoids refactoring `append_context_key` to return root_id).
3. **Doesn't disturb time/language**: `append_current_time` and `append_user_language` remain the last two operations — consistent with existing behavior.

### Why graceful degradation (try/except → skip)?

The post-processing chain runs on EVERY agent spawn. If the metadata fetch fails (DB error, repository not initialized, etc.), the agent must still be able to spawn. Wrapping in try/except and returning the unmodified prompt ensures no spawn is blocked by metadata injection failures.

### Why `hasattr(manager, 'get_shared_context_repository')` guard?

During the transition period (or in tests where manager is mocked), the method may not exist. The `hasattr` guard ensures backward compatibility — if the repository accessor isn't available, injection is silently skipped.

### Why `---` separator AFTER metadata?

The `---` visually separates the shared context metadata from the rest of the system prompt (time, language, and the actual agent prompt content). This makes it clear to the LLM where "shared context" ends and "regular prompt" begins.

### Why JSON format for KV pairs (not key: value lines)?

1. **Handles complex values**: Values can be strings, numbers, booleans, objects, arrays. JSON handles all types uniformly.
2. **Easy to parse**: If an agent needs to reference a specific value, JSON is structured and parseable.
3. **Compact**: For small KV sets, JSON is concise and readable.

## Constraints

- Must NOT crash agent spawn if metadata fetch fails — always graceful degrade.
- Must NOT inject empty section when no metadata exists.
- Must run at BOTH spawn and restore call sites (identical logic).
- Must be positioned between `append_context_key()` and `append_current_time()`.
- Must add `---` separator after the metadata content.
- Must be post-cache (does not invalidate PromptCache — same as `append_context_key`).

## Deliverables

- [ ] `append_shared_context_metadata()` function defined in `instance_lifecycle.py`
- [ ] Function handles empty metadata (returns prompt unchanged)
- [ ] Function handles errors gracefully (try/except → skip)
- [ ] Function formats metadata as JSON in `# Shared Context` section
- [ ] `---` separator added after metadata content
- [ ] Spawn call site updated (~line 560)
- [ ] Restore call site updated (~line 1523)
- [ ] Repository accessed via `manager.get_shared_context_repository()`
- [ ] `hasattr` guard for backward compatibility
