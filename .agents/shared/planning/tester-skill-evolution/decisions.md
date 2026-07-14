# Architecture Decisions & Rollback Considerations

## Architecture Decisions

### D1: Skill Bank as Transparent Infrastructure (Not a Layer)

**Decision**: The Skill Bank is NOT visible to the agent as a "layer." It's a template store that the clone-on-miss mechanism reads from transparently.

**Rationale**: The agent's mental model is simple — two layers (innate + evolvable skills). Introducing the bank as a third visible concept adds cognitive load without value. The clone operation is an implementation detail.

**Alternatives Considered**:
- Bank as Layer 0 (below innate): Rejected — adds complexity, agent doesn't need to know about templates
- Bank visible via API only (current state): Rejected — bank would remain dead-end store

### D2: Clone-on-Miss vs Eager Clone at Startup

**Decision**: Clone-on-miss (lazy). Skills are cloned from bank to evolution table only when first needed.

**Rationale**: 
- Eager clone (at startup for all projects) would create rows for projects that never use the tester
- Clone-on-miss is naturally per-project and per-skill
- First-use cost is negligible (one DB insert + embedding computation)

**Trade-off**: First message to tester in a project pays clone cost. Acceptable for POC.

### D3: auto_load Skills in System Prompt vs Injected Message

**Decision**: auto_load skills go directly into the system prompt (section #4.5), NOT as HumanMessage injections.

**Rationale**:
- System prompt is persistent across the conversation — foundational skills should always be visible
- HumanMessage injections are transient (appear before one message, then scroll away)
- auto_load skills are "always-on" — system prompt is the correct placement

### D4: Post-Cache Append for auto_load Skills (C1 Revision)

**Decision**: auto_load skills are injected via a post-cache `append_auto_load_skills()` function — matching the existing `append_context_key` / `append_current_time` / `append_user_language` pattern. `compose_system_prompt()` and `PromptCache` are NOT modified.

**Rationale**:
- The PromptCache key is `(agent_id, mcp_tool_names)` + file mtimes — it does NOT include `project_id`
- Different projects have different auto_load skills — baking them into the cached prompt would cause cache key collisions
- The post-cache append pattern is already used for per-instance/per-project content (context_key, shared_metadata, time, language)
- Zero risk to existing caching infrastructure

**Alternatives Considered**:
- ~~Include auto_load in compose_system_prompt()~~: REJECTED — causes cache collision in multi-project deployments
- ~~Add project_id to cache key~~: REJECTED — invalidates cache on every project switch, defeats caching purpose
- ~~Bypass cache for auto_load~~: REJECTED — unnecessary; post-cache append is simpler and proven

### D5: Sync + Async Clone Methods

**Decision**: Provide BOTH sync and async clone methods. Sync for the prompt loader context (`instance_lifecycle.py`), async for the injection pipeline (`instance_messaging.py`).

**Rationale**:
- `append_auto_load_skills()` runs in a sync context (called from `spawn_instance` path)
- The injection pipeline in `instance_messaging.py` is async
- Repositories are already sync — async wrappers just bridge via `asyncio.to_thread`

### D6: Seeding Not Gated by skill_evolution Config

**Decision**: Startup seeding (`SkillSeedService`) runs independently of `config.skill_evolution`. It only needs `skill_bank_repo` which is always available.

**Rationale**:
- Skill Bank is standalone CRUD infrastructure (per existing design)
- Seeding only writes to `skill_bank` table, not the evolution tables
- Clone-on-miss (which DOES need skill_evolution) is a separate concern
- If skill_evolution is not configured, skills are seeded but never cloned — no harm

### D7: source_skill_bank_id as Soft FK

**Decision**: `skills.source_skill_bank_id` is a soft foreign key (no DB CONSTRAINT FK).

**Rationale**:
- skill_bank rows can be deleted independently (user CRUD)
- A hard FK would prevent deleting bank templates that have been cloned
- The soft FK is for audit/lineage only — if the template is deleted, the cloned skill remains valid

### D8: auto_load Flag Propagation Path (C2 Revision)

**Decision**: The `auto_load` flag lives on BOTH tables and flows through a clear chain: skill-set.md → skill_bank.auto_load → skills.auto_load (via clone). The clone operation reads `template.auto_load` — never hardcoded.

**Rationale**:
- skill-set.md is the source of truth for which skills are auto_load
- Seeding (P3) stores auto_load on skill_bank during template insertion
- Clone (P4) reads auto_load from the bank template and sets it on the cloned skill
- This ensures the flag is always correct without code-level defaults

**Alternatives Considered**:
- ~~Hardcode auto_load=False in clone, set later~~: REJECTED — feature would never activate
- ~~Store auto_load in a separate config file~~: REJECTED — skill-set.md already defines it
- ~~Read auto_load from skill-set.md at clone time~~: Rejected — requires file I/O at runtime; bank already has the value

## Risk Mitigations

### PostgreSQL Migration Pattern

**Risk**: `.sql` migrations are NO-OP on PostgreSQL. New columns must also be added via `_ensure_postgres_columns()`.

**Mitigation**: Every new column has THREE paths:
1. Model definition (`models.py`) — for fresh DBs via `create_all()`
2. SQLite `.sql` migration — for existing SQLite DBs
3. PG `_ensure_postgres_columns()` — for existing PostgreSQL DBs

**Verification**: Test on BOTH SQLite (in-memory) and PostgreSQL. The `_ensure_postgres_columns()` method runs on every startup — any syntax error blocks daemon startup.

### Injection Pipeline Modification

**Risk**: Modifying `instance_messaging.py:1915` could break the existing skill injection flow.

**Mitigation**:
- Clone-on-miss runs BEFORE the existing injection code (additive, not replacing)
- Clone-on-miss is wrapped in try/except — failures are logged but don't block injection
- If clone fails, the injection pipeline proceeds normally (just with empty skills table)

### Prompt Composition Change

**Risk**: Adding a section to `compose_system_prompt()` changes the prompt structure for ALL agents, not just tester.

**Mitigation**:
- The `auto_load_skills` parameter defaults to `""` — existing callers see no change
- Only tester (and future agents with auto_load skills) get the new section
- The section is empty unless skills exist — no blank sections in prompts

---

## Rollback Considerations

### Rollback by Phase

| Phase | Rollback Action | Impact |
|-------|----------------|--------|
| **P1** (Content) | Delete `skills-template/` + `skill-set.md` | No runtime impact — just files |
| **P2** (Schema) | Columns are additive with defaults — no rollback needed | Columns remain, harmless if unused |
| **P3** (Seeding) | Remove seeding call from `initialize()`. Bank rows remain but are unused | No runtime impact |
| **P4** (Clone) | Remove clone service. Injection pipeline reverts to searching empty skills table | No skills found, injection no-ops |
| **P5** (Prompt) | Revert `compose_system_prompt()` signature. Set `auto_load_skills=""` | No auto_load section, skills still in DB |
| **P6** (Wiring) | Remove `skill_injection: true` + `dynamic-skill` from meta.json | Injection disabled, prompt reverts |
| **P7** (Innate) | Remove added sections from skill.md files | Prompt reverts to original content |

### Safe Rollback Order (if full feature needs removal)

1. **P6 first** — disable skill_injection (stops new injections immediately)
2. **P5** — remove auto_load section from prompt (stops loading skills into prompt)
3. **P4** — remove clone service (stops creating new skill rows)
4. **P3** — remove seeding (stops refreshing bank)
5. **P7** — remove innate skill updates
6. **P1** — remove template files
7. **P2** — columns can remain (additive, harmless)

**Key Insight**: Every change is **additive**. No existing functionality is removed or modified destructively. Full rollback = revert config flags + remove new code, leaving DB schema and data intact.

### Database State After Rollback

- `skill_bank` table: rows remain (harmless templates)
- `skills` table: cloned rows remain (harmless, just unused)
- New columns: remain (additive with defaults, no harm)
- No data loss, no schema corruption

### Rollback Verification

After rollback, verify:
1. Existing agents (developer, wanderer, etc.) work unchanged
2. Tester agent works without skill injection (reverts to prompt-only knowledge)
3. All existing tests pass
4. No orphaned references to removed services

---

## Implementation Sequencing Summary

```
Week 1, Day 1 (Morning):
  ├── P1: Author skill templates (3h)
  ├── P2: Schema changes (2h) ← parallel with P1
  └── P7: Innate skill updates (1h) ← parallel with P1

Week 1, Day 1 (Afternoon):
  └── P3: Startup seeding (3h) ← after P1 + P2

Week 1, Day 2 (Morning):
  └── P4: Clone-on-miss (4h) ← after P2 + P3

Week 1, Day 2 (Afternoon):
  └── P5: auto_load prompt section (2h) ← after P2 + P4

Week 1, Day 2 (End):
  ├── P6: Tester wiring (0.5h) ← after P5
  └── Integration testing + manual verification (1.5h)

Total: ~2.5 developer-days
```
