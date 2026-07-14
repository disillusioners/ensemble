# Skill Bank Index Name Mismatch

## Date
2026-07-14

## Root Cause
The SkillBankItem model defines an index via `Index("ix_skill_bank_project_id", "project_id")` in `__table_args__`.
SQLModel/SQLAlchemy uses `ix_` prefix convention for auto-generated index names. However, the raw PostgreSQL DDL
in `manager.py:_ensure_postgres_columns()` used a different name: `idx_skill_bank_project`. The SQLite migration
file used yet another name: `idx_skill_bank_project`.

This means on PostgreSQL, the index created by `SQLModel.metadata.create_all()` (on fresh DBs) would be named
`ix_skill_bank_project_id`, but the index created by the raw DDL (on existing DBs) would be named
`idx_skill_bank_project`. Both would exist simultaneously — redundant and confusing.

## Fix Applied
Aligned all three index name references to `ix_skill_bank_project_id`:
1. `daemon/manager.py` PG DDL: `idx_skill_bank_project` → `ix_skill_bank_project_id`
2. `daemon/migrations/versions/20260713_000001_create_skill_bank.sql`: matching rename

## Before/After
- Before: 3 different index names across model, SQLite migration, and PG DDL
- After: 1 consistent index name `ix_skill_bank_project_id` everywhere

## Commit
5166752b

## Lesson
When adding a new table with `Index()` in `__table_args__`, verify the index name matches across:
1. The SQLModel `__table_args__` definition
2. The raw PG DDL in `_ensure_postgres_columns()`
3. The SQLite migration `.sql` file

SQLModel uses `ix_` prefix; raw DDL should follow the same convention.
