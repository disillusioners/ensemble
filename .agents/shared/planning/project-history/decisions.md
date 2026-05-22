# Architecture Decisions: Project History

## Decision 1: Separate Table vs JSON Column
**Decision:** New `project_history` SQLModel table with foreign key to `projects`.

**Options Considered:**
| Option | Pros | Cons |
|--------|------|------|
| JSON column on projects | Simple, no migration, follows CE pattern | Can't page efficiently, search is slow, unbounded growth in single column, duplicates data on every project load |
| Separate table | Clean separation, efficient paging/search, indexed queries, scalable | New migration, new repository methods, more code |

**Rationale:** Critical experience works as JSON because it's capped at 30 entries and doesn't need search. History has no cap, needs paging, needs search — a separate table is the right call.

## Decision 2: Entry Type as Enum vs Free-text
**Decision:** String field validated against `HistoryEntryType` enum values.

**Rationale:** Provides structure for categorization and filtering while remaining extensible. New types can be added to the enum without migration.

## Decision 3: Context Injection Limit
**Decision:** 10 most recent entries in project context injection.

**Rationale:** 20 entries would be ~40-60 lines of context which is excessive. 10 entries at ~1 line each is ~10 lines — reasonable. Agents can use the list tool to see more.

## Decision 4: LIKE-based Search (Not FTS5)
**Decision:** Use SQLAlchemy `ilike` for search across summary and details fields.

**Rationale:** SQLite FTS5 would be overkill for this use case. LIKE-based search covers the common cases (keyword search). Can upgrade later if needed. No additional migration or virtual table setup required.

## Decision 5: Tool Availability — All Agents
**Decision:** All agents get project history tools, same as project tools.

**Rationale:** While primarily used by leader agents, restricting access adds configuration complexity without clear benefit. Any agent may legitimately record project events (e.g., a worker completing a subtask).

## Decision 6: No Auto-eviction
**Decision:** History entries are never auto-deleted. Agents can manually delete entries.

**Rationale:** Unlike critical experience (which auto-evicts at 30 entries), history is meant to be a complete chronological record. Storage is cheap; losing history is expensive. If cleanup is needed later, add a pruning tool.
