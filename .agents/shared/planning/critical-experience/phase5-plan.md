# Phase 5: Testing & Integration

## Objective
Validate the entire critical experience feature end-to-end: schema, tools, agent routing, and injection. Ensure merge logic, eviction, and access control all work correctly.

## Coupling
- **Depends on**: All phases (1-4)
- **Coupling type**: **tight** — integration testing requires all implementations complete
- **Shared files with other phases**: Tests touch all code from Phases 1-4
- **Shared APIs/interfaces**: None (tests are consumers)
- **Why this coupling**: Integration tests validate the entire feature stack

## Context
- All prior phases completed
- Feature branch: `feature/critical-experience`
- Testing strategy: Manual integration test + verification of key behaviors

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Test Phase 1: Schema & Migration | Start daemon with existing DB → verify migration runs → verify `critical_experience` column exists → verify `to_dict()` includes field → verify `to_data()` bug is fixed | Manual / script |
| 2 | Test Phase 2: Tool Operations | Use Leader or _mother agent to call: (a) `project_ce_add` — add 3 entries with different categories → verify returned entries; (b) `project_ce_add` with similar entry → verify merge; (c) `project_ce_list` → verify returns all; (d) `project_ce_remove` → verify removal; (e) Add 31 entries → verify eviction | Manual via API |
| 3 | Test Phase 2: Access Control | Verify that an agent without `critical_experience` in tools.allow (e.g., coder) does NOT see these tools in their tool list. Verify Experiencer and Leader DO see them. | Manual via API |
| 4 | Test Phase 3: Experiencer Routing | Send a message to the Experiencer agent containing knowledge that should be routed to critical experience (e.g., "Always use yarn, not npm for this project — it's the standard"). Verify: (a) RAG insertion happens, (b) `project_ce_add` is called, (c) Entry appears in project's critical experience list. | Manual via API |
| 5 | Test Phase 3: Experiencer Non-Routing | Send general programming knowledge to Experiencer (e.g., "REST APIs use HTTP methods"). Verify `project_ce_add` is NOT called (only RAG insertion). | Manual via API |
| 6 | Test Phase 4: Injection | Spawn an instance with a project that has critical experience entries. Verify the injected project context includes the formatted critical experience section. | Manual via API |
| 7 | Edge Case: Empty Project | Create a new project → verify `critical_experience` is `[]` in to_dict() → verify `project_ce_list` returns `[]` → verify injection works with empty list. | Manual via API |
| 8 | Edge Case: Summary Length | Try `project_ce_add` with summary > 200 chars → verify error returned. | Manual via API |
| 9 | Edge Case: Invalid Category/Priority | Try `project_ce_add` with invalid category or priority → verify validation error. | Manual via API |

## Key Test Scenarios Matrix

| Scenario | Input | Expected Outcome |
|----------|-------|-----------------|
| Add new entry | category=convention, priority=high, summary="Use yarn not npm" | Entry created with UUID, timestamps, returned |
| Add similar entry | category=convention, summary="Always use yarn instead of npm" | Merged with existing, updated_at changed, created_at preserved |
| Add different category | category=risk, summary="DB migrations run on startup" | New entry created (different category) |
| List entries | project_ce_list(project_id=...) | Returns all entries as list of dicts |
| Remove entry | project_ce_remove(project_id=..., entry_id=...) | Entry removed, confirmation returned |
| Remove non-existent | project_ce_remove(project_id=..., entry_id="fake") | Error returned |
| Max entries exceeded | Add 31st entry | Oldest medium-priority entry evicted |
| Overflow with merge | Add similar entry when at 30 | Merge happens (no new entry), no eviction needed |
| Invalid summary length | summary with 250 chars | Error: "Summary must be ≤200 chars" |
| Invalid category | category="unknown" | Error: "Invalid category 'unknown'" |
| Non-authorized agent | Coder tries to use CE tools | Tool not available (filtered out) |

## Test Execution Plan

### Step 1: Verify Migration
```bash
# Start daemon — migration should auto-apply via MigrationRunner
# Check SQLite schema
sqlite3 <db_path> ".schema projects" | grep critical_experience

# Verify migration tracked
sqlite3 <db_path> "SELECT * FROM schema_migrations WHERE version LIKE '20260520%'"
```

### Step 2: Tool Tests via Leader
```bash
# Using the HTTP API, send messages to a Leader instance:
# 1. Add entry
# 2. Add similar entry → check merge
# 3. List entries
# 4. Remove entry
# 5. Add 31 entries → check eviction
```

### Step 3: Experiencer Routing
```bash
# Send message to Experiencer with project-scoped knowledge
# Check project's critical_experience list for new entry
```

### Step 4: Injection Check
```bash
# Spawn instance with project that has CE entries
# Check that injected context includes:
#   1. critical_experience in the JSON block
#   2. Structured "### ⚡ Critical Experience" section with priority icons
#   3. Section is omitted when project has no CE entries
```

## Constraints
- Testing is manual (no automated test suite exists in this project based on exploration)
- Feature branch should be clean and ready for merge after all tests pass
- Document any issues found during testing

## Deliverables
- [ ] Migration verified: column exists, default `[]`, tracked in `schema_migrations` table
- [ ] Tool CRUD operations verified: add, merge, list, remove
- [ ] Eviction verified: oldest lowest-priority removed at 31 entries (evict-before-append sequence)
- [ ] Validation verified: summary length, category, priority
- [ ] Access control verified: Experiencer/Leader have tools, others don't
- [ ] Experiencer routing verified: critical knowledge → CE, general → RAG only
- [ ] Injection verified: entries appear in JSON block AND structured "⚡ Critical Experience" section
- [ ] Empty project verified: no CE section shown when list is empty
- [ ] Edge cases verified: invalid inputs, non-existent entries
- [ ] Defensive fix verified: `to_data()` no longer called anywhere
