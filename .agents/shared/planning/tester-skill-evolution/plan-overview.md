# Plan Overview: Tester Skill Evolution System

## Objective

Build a two-layer skill system for the tester agent: immutable innate skills (Layer 1) + evolvable skills (Layer 2) that bridge the isolated Skill Bank to the evolution pipeline via clone-on-miss. The 9 tester skills start as versioned templates in the Skill Bank, get cloned into project-scoped skills on first use, and evolve over time via the existing skill-keeper agent.

## Scope Assessment

**LARGE** — 7 implementation phases touching schema, services, prompt composition, agent configuration, and content authoring. Span: ~15 files modified, ~20 new files created. Estimated 2-3 developer-days.

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **DB**: PostgreSQL PRIMARY (SQLite for tests). All schema changes use the three-path dual-driver pattern.
- **Config gating**: `config.skill_evolution` controls all skill repos/services.

## Two-Layer Model (Agent POV)

```
Layer 1: INNATE (immutable, always in system prompt)
  └─ compose_system_prompt() section #4 — from agents/_prompt_system/innate-skills/

Layer 2: SKILL (evolvable, from skill system)
  ├─ auto_load: true  → Post-cache append_auto_load_skills() — loaded per-spawn per-project
  └─ auto_load: false → On-demand via skill_injection pipeline (BM25→embedding→LLM)
```

**Skill Bank = transparent infrastructure** (clone-on-miss invisible to agent).

**Key Architecture Decisions (Revision 2)**:
- **C1 fix**: auto_load uses post-cache append pattern (like `append_context_key`, `append_current_time`) — NOT baked into `compose_system_prompt()`. Avoids PromptCache key collisions in multi-project deployments.
- **C2 fix**: `auto_load` stored on BOTH `skill_bank` (template) AND `skills` (cloned copy). Clone reads from template — never hardcoded.
- **C3 fix**: skill-set.md parser fully implemented with YAML frontmatter + PyYAML.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Skill Content Authoring | Write 9 skill templates + skill-set.md + skills-template/ dir | None | — (root) | 3h |
| 2 | Schema Changes | Add `template_version`+`agent_id`+`auto_load` to skill_bank; `auto_load`+`source_skill_bank_id` to skills (5 columns total) | None | independent of P1 | 2h |
| 3 | Startup Seeding | Versioned, idempotent seeding with full YAML parser | P1 (templates), P2 (schema) | tight | 3h |
| 4 | Clone-on-Miss | SkillCloneService with auto_load propagation + sync/async methods | P2 (schema), P3 (seeded templates) | tight | 4h |
| 5 | auto_load Prompt Section | Post-cache `append_auto_load_skills()` in instance_lifecycle.py | P2 (schema), P4 (clone) | tight | 2h |
| 6 | Tester Wiring | meta.json: skill_injection + dynamic-skill | P5 (auto_load section) | loose | 0.5h |
| 7 | Innate Skill Updates | test-pack + dynamic-skill innate modifications | None | independent (tested with P6) | 1h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| P1 → P3 | tight | P3 reads template files + skill-set.md P1 creates |
| P2 → P3 | tight | P3 needs `template_version`, `agent_id`, `auto_load` columns |
| P2 → P4 | tight | P4 reads `auto_load`, `source_skill_bank_id` columns |
| P3 → P4 | tight | P4 clones from skill_bank rows P3 seeds |
| P2 → P5 | tight | P5 reads `auto_load` column on skills |
| P4 → P5 | tight | P5 calls `ensure_auto_load_skills_sync()` from P4 |
| P5 → P6 | loose | P6 flips config flag; P5 code must exist |
| P1, P7 | independent | Can run parallel with any phase |
| P6 ↔ P7 | test coupling (W5) | Tested together in integration test |

### Parallelization Opportunities

```
Time →   T0        T1        T2        T3        T4        T5
P1 ────────────┤
P2 ────────────┤
P7 ────────────┤ (independent)
               P3 ────────────┤
                              P4 ────────────────┤
                                                  P5 ──────┤
                                                           P6 ─┤
                                                           P6+P7 test ─┤
```

- **P1 + P2 + P7** fully parallel (no shared files, no shared APIs)
- **P3** waits for P1 + P2
- **P4** waits for P2 + P3
- **P5** waits for P2 + P4
- **P6** waits for P5; tested together with P7 (W5)

## Schema Changes Summary (5 columns)

| Table | Column | Type | Default | Purpose |
|-------|--------|------|---------|---------|
| `skill_bank` | `template_version` | TEXT | `'1.0.0'` | Semver for idempotent seeding |
| `skill_bank` | `agent_id` | TEXT (nullable) | NULL | Which agent owns this template |
| `skill_bank` | `auto_load` | BOOLEAN/INT | false | Whether cloned skills should auto_load |
| `skills` | `auto_load` | BOOLEAN/INT | false | Whether this skill is in system prompt |
| `skills` | `source_skill_bank_id` | TEXT (nullable) | NULL | Soft FK to skill_bank template |

Each column requires THREE paths: model definition, SQLite `.sql` migration, PG `_ensure_postgres_columns()`.

## Key Architecture: Post-Cache Append Pattern (C1 fix)

```
load_and_cache_prompt()           ← CACHED (key: agent_id + mcp_tools + file mtimes)
    │
    ▼ system_prompt
append_context_key()              ← post-cache (per-instance)
append_shared_context_metadata()  ← post-cache (per-instance)
append_current_time()             ← post-cache (per-spawn)
append_user_language()            ← post-cache (per-project)
append_auto_load_skills()         ← post-cache (per-project) ★ NEW
    │
    ▼ final system_prompt
```

auto_load skills are appended AFTER cache retrieval, using `project_id` explicitly. No PromptCache modification needed.

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **PostgreSQL migration NO-OP** | HIGH | High | Three-path pattern: model + SQLite .sql + PG `_ensure_postgres_columns()` |
| **PromptCache collision (C1)** | HIGH | ~~High~~ → Eliminated | Post-cache append pattern — cache key unchanged |
| **auto_load hardcoded (C2)** | HIGH | ~~High~~ → Eliminated | auto_load on skill_bank template, read during clone |
| **Parser stub (C3)** | HIGH | ~~High~~ → Eliminated | Full `parse_skill_set_file()` with YAML + error handling |
| **Injection pipeline modification** | MEDIUM | Low | Clone-on-miss before search, wrapped in try/except |
| **Evolution config disabled** | HIGH | Medium | Log warning at startup; document config requirement |
| **UniqueConstraint conflict on clone** | MEDIUM | Low | Clone checks `get_by_name()` first — only clones if missing |

## Success Criteria

- [ ] 9 skill templates exist in `agents/tester/skills-template/`
- [ ] `skill-set.md` uses YAML frontmatter format with all 9 skills
- [ ] `skill_bank` table has `template_version`, `agent_id`, `auto_load` (SQLite + PG)
- [ ] `skills` table has `auto_load`, `source_skill_bank_id` (SQLite + PG)
- [ ] Startup seeds all 9 templates (idempotent, version-aware, W4 guard)
- [ ] Clone-on-miss propagates `auto_load` from template (C2 — no hardcoding)
- [ ] `append_auto_load_skills()` post-cache function works (C1 — no cache change)
- [ ] Tester meta.json has `skill_injection: true` + `dynamic-skill` innate skill
- [ ] test-pack + dynamic-skill innate skills updated (P7)
- [ ] P6+P7 integration test passes (W5)
- [ ] All existing tests pass (no regression)

## Test Strategy

### Unit Tests
- `tests/unit/test_skill_bank_repository.py` — extend with new columns + queries
- `tests/unit/test_skill_repository.py` — extend with `auto_load` + `source_skill_bank_id`
- **NEW** `tests/unit/test_skill_clone_service.py` — clone-on-miss, auto_load propagation (C2)
- **NEW** `tests/unit/test_skill_seeding.py` — parser (C3), idempotency (W4), version bump
- **NEW** `tests/unit/test_append_auto_load_skills.py` — post-cache append (C1)

### Integration Tests
- `tests/test_innate_skills_refactoring.py` — verify prompt composition unchanged
- `tests/test_registry_skill_injection.py` — tester resolves with skill_injection=true
- **NEW** `tests/test_tester_skill_chain.py` — P6+P7 end-to-end (W5)

### Manual Verification
- Start daemon → 9 skill_bank rows seeded (check `/api/skill-bank?category=tester-skill-set`)
- Spawn tester → verify auto_load section in system prompt
- Send task → verify on-demand skill injection works

## Tracking

- Created: 2026-07-14
- Last Updated: 2026-07-14 (Revision 2 — C1/C2/C3 fixes + W1-W5)
- Status: draft (revision 2)
