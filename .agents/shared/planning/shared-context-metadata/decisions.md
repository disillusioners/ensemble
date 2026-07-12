# Architecture Decisions: Shared Context Metadata KV System

## D1: New Domain `shared_context/` vs. Extending `project/` Domain

**Decision**: Create a new repository domain at `daemon/repositories/shared_context/`.

**Rationale**:
- Project metadata is scoped by `project_id` and couples to `Project.updated_at` mutation + `_enrich_project()` enrichment. This coupling is inappropriate for context-key-scoped metadata.
- Shared context metadata is scoped by `context_key` (instance tree root ID) — a completely different partition key.
- Separate domain allows independent evolution, testing, and lifecycle.
- Follows existing domain-per-directory pattern (`project/`, `instance/`, `job_queue/`, `skill/`, etc.).

**Alternatives considered**:
- *Extend project domain*: Would mix two different partition keys (project_id vs context_key) in one repository. Would inherit unwanted project coupling.
- *Extend instance domain*: Instance repository is already large. Metadata is not instance-scoped — it's tree-scoped.

---

## D2: Tool Category `context_metadata` vs. Reusing `context` Category

**Decision**: Create a new tool category `context_metadata`, separate from the existing `context` category.

**Rationale**:
- The existing `context` category (`list_context`, `read_context`) provides file-based context access — reading markdown files from the shared context directory.
- The new `shared_context_metadata` tool provides DB-backed KV CRUD — a fundamentally different storage mechanism and access pattern.
- Separating categories allows fine-grained access control: an agent might have file context access but not metadata write access (or vice versa).
- The `context` category is currently NOT in leader's `tools.allow` — adding metadata to it would require also granting file context access, which may be undesirable.

**Alternatives considered**:
- *Reuse `context` category*: Would conflate file-based and DB-based context tools. Leader would get both or neither.
- *Use innate skill mapping*: Would require creating a new innate skill just for this tool — overkill for a single tool.

---

## D3: Injection Position — After `append_context_key()`, Before `append_current_time()`

**Decision**: Insert `append_shared_context_metadata()` between `append_context_key()` and `append_current_time()` in the post-processing chain.

**Rationale**:
- Metadata is scoped by `context_key` — logically adjacent to the Context Key section.
- `append_context_key()` already resolves `root_id` (which IS the context_key). Placing the metadata injection right after means the context_key is fresh in scope.
- `append_current_time()` and `append_user_language()` remain last — consistent with existing behavior. Time should always be the most recent timestamp.
- The `---` separator after metadata content provides a clean visual boundary before the time/language sections.

**Alternatives considered**:
- *After `append_user_language()` (last)*: Would place metadata far from the Context Key section. Less coherent reading order.
- *Before `append_context_key()`*: Metadata references context_key, so it should come after the key is established.

---

## D4: Single Tool with `operations` Array vs. Separate Tools

**Decision**: Single `shared_context_metadata` tool accepting a JSON `operations` array with `set`/`delete`/`list` op types.

**Rationale**:
- **Batch efficiency**: Leader can set multiple KV pairs in one call (e.g., `project_change_scope` + `decision` + `priority`).
- **Atomic mental model**: All operations in one call are conceptually one "metadata update" event.
- **Fewer tool registrations**: One tool vs three — simpler for LLM to discover and use, less token overhead in tool descriptions.
- **Matches user requirement**: "The tool supports batch create/update/delete of multiple KV pairs in one call."
- **Precedent**: `todo_graph_add_subtask` accepts JSON-encoded arrays — established pattern in the codebase.

**Alternatives considered**:
- *Three separate tools* (`set_context_metadata`, `delete_context_metadata`, `list_context_metadata`): More verbose, no batch capability, three tool descriptions instead of one.
- *Tool with typed parameters*: LangChain `@tool` doesn't handle variadic/dict parameters well. JSON string is the cleanest approach.

---

## D5: Auto-resolve `context_key` vs. Pass as Parameter

**Decision**: The tool auto-resolves `context_key` from the instance tree root via `instance_repository.get_tree_root_id(current_instance_id)`. The agent does NOT pass context_key as a parameter.

**Rationale**:
- **Consistency**: `context_key` is already injected into the system prompt via `append_context_key()`. The tool should use the same resolution mechanism.
- **Safety**: Prevents an agent from accidentally writing to a different context_key's metadata.
- **Simplicity**: Agent doesn't need to extract context_key from its own system prompt and pass it back.
- **Precedent**: `context_tools.py` (`list_context`, `read_context`) require `context_key` as a parameter — but those are read-only tools for cross-context access. The metadata tool is write-oriented and should be scoped to the caller's own context.

**Alternatives considered**:
- *Require context_key as explicit parameter*: More flexible but error-prone. Adds cognitive load on the agent.

---

## D6: JSON Format for Metadata Values vs. String-Only

**Decision**: Metadata values use `JSONBType` (JSONB on PostgreSQL, JSON on SQLite) — supporting any JSON type (string, number, boolean, null, array, object).

**Rationale**:
- **Flexibility**: Metadata values may be complex (e.g., `{"components": ["auth", "api", "db"]}` or `{"priority": 1, "blocking": true}`).
- **Consistency**: `project_metadata_records` uses the same `JSONBType` for `meta_value`.
- **Query-friendly**: JSONB on PostgreSQL supports `->>` and `@>` operators for future querying needs.

**Alternatives considered**:
- *String-only values*: Would require agents to serialize/deserialize JSON themselves. Loses type information.

---

## D7: Graceful Degradation on Injection Failure

**Decision**: `append_shared_context_metadata()` wraps the metadata fetch in try/except. On any error (DB failure, repository not initialized, etc.), it returns the system prompt unchanged — no section is injected.

**Rationale**:
- The post-processing chain runs on EVERY agent spawn. A failure in metadata injection must NOT block agent creation.
- This matches the pattern of `append_context_key()` which has fallback logic (`root_id = parent_id` if not found).
- Empty metadata (no KV pairs) also results in no injection — no empty `# Shared Context` header.

**Alternatives considered**:
- *Raise on error*: Would block agent spawn. Unacceptable for a non-critical feature.
- *Inject error message*: Would pollute the system prompt with error text. Confusing for the LLM.

---

## D8: Leader-Only Tool Availability (Initial Scope)

**Decision**: Add `"context_metadata"` to leader's `tools.allow` only. Other agents (developer, reviewer, tester) do NOT get the tool initially.

**Rationale**:
- The leader is the coordinator who sets shared context for the team. Team members consume the metadata via injection — they don't need to write it.
- This follows the principle of least privilege.
- Can be extended later by adding `"context_metadata"` to other agents' `tools.allow` if needed.

**Alternatives considered**:
- *All agents get the tool*: Unnecessary write access for team members. Could lead to conflicting metadata writes.
- *Innate skill mapping*: Would require a new innate skill — overkill for initial scope.

---

## D9: `---` Separator After Metadata Content

**Decision**: Add a `---` (horizontal rule) separator immediately after the metadata JSON content, before the next system prompt section (Current Time).

**Rationale**:
- Visually separates shared context metadata from the rest of the system prompt.
- Makes it clear to the LLM where "shared context" ends and "regular prompt sections" begin.
- The `append_context_key()` function already uses `\n---\n` as a section delimiter — consistent pattern.

**Format**:
```markdown
## Context Key

CONTEXT_KEY: root-123

# Shared Context

context_key: root-123

## Metadata KV

{"project_change_scope": "BIG", "decision": "use OAuth2"}

---

## Current Time

ISO: 2026-07-12T10:40:26+00:00
```
