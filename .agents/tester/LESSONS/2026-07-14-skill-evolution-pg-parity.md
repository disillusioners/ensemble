# Lesson: Skill Evolution PostgreSQL Parity (2026-07-14)

## Context
Feature: tester-skill-evolution — new skill bank seeding, clone-on-miss, auto_load injection.

## Finding
The new skill evolution code is fully PostgreSQL-compatible out of the box. The dual-driver repository pattern (SkillBankRepository, SkillRepository using SQLModel) works correctly on PG without any code changes.

### Key Verifications
1. **Schema types**: `auto_load` is `boolean` (PG native), IDs are `varchar(64)`, names are `varchar(256)`, `skill_embeddings.embedding` is `jsonb` — all correct.
2. **Constraints**: UniqueConstraint("project_id", "name", "generation") → UNIQUE btree works on PG.
3. **FK cascades**: skill_lineage, skill_usage_records, skill_embeddings, skill_ab_tests — all preserved.
4. **Seeding idempotency**: W4 version guard (`_version_lt`) works identically on PG.
5. **Clone idempotency**: UNIQUE constraint prevents duplicates; second clone returns existing row.

## Root Cause (Non-Issue)
No issue found. The codebase's existing dual-driver pattern handles PG correctly.

## Test DB Migration Note
The `ensemble_test` PG database may be missing the new columns (`auto_load`, `source_skill_bank_id`) and `skill_bank` table if it was created before the feature branch. Running `SQLModel.metadata.create_all()` or starting the daemon (which triggers `_ensure_postgres_columns()`) adds them. This is expected behavior, not a bug.

## Impact
- Future PG tests for skill evolution can use `test/pg_skill_schema_check.py` as a harness.
- The `skill_evolution_pg_test.sh` pack collects `-m postgres` marked tests (currently 0, but ready for future PG-marked tests).
