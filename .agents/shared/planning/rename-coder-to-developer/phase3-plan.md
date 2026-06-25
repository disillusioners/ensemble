# Phase 3: DB Migration & Backward Compatibility (Rev. 2)

> **Revision 2**: Fixes C1 (PostgreSQL no-op), C2 (phantom table), C3 (missing tables), S1 (incomplete alias), S2 (checkpoint audit), S4 (undefined function).

## Objective
Ensure backward compatibility for existing database records and external API consumers that reference `agent_id="coder"`. Implement a registry-level alias fallback across ALL resolution methods and a dual-engine DB migration covering all 6 tables with agent_id columns.

## Coupling
- **Depends on**: Phase 2 (daemon source must reference the new agent_id)
- **Coupling type**: tight
- **Shared files with other phases**: `daemon/registry.py` (modified in Phase 2 for docstrings, modified here for alias logic)
- **Shared APIs/interfaces**: `registry.resolve_to_id()`, `resolve_pure_id()`, `resolve_path_to_id()`, `exists()`
- **Why this coupling**: Backward-compat alias must be implemented after the agent rename is complete

## Context

### DB Tables with agent_id Columns (C2/C3 Corrected — 6 tables)

| Table | Model Class | agent Columns | PK Column |
|-------|-------------|---------------|-----------|
| `instances` | `Instance` (`instance/models.py:47`) | `agent_id`, `agent_dir` | `instance_id` |
| `instance_mappings` | `InstanceMapping` (`source/models.py:87`) | `agent_id`, `agent_dir` | (mapping_id) |
| `job_queue_items` | `JobItem` (`job_queue/models.py:114`) | `agent_id`, `agent_dir` | `job_id` |
| `dead_letter_items` | `DeadLetterItem` (`job_queue/models.py:316`) | `agent_id`, `agent_dir` | (id) |
| `projects` | `Project` (`project/models.py:190`) | `creator_agent_id` | `id` |
| `jobqueue` | (legacy table, may not exist) | `agent_id`, `agent_dir` | `job_id` |

> **REMOVED**: `task_queue_items` — does NOT exist in the codebase (verified: 0 grep matches).
> **ADDED**: `dead_letter_items` — has both `agent_id` and `agent_dir` columns (was missed in Rev. 1).

### LangGraph Checkpoint Audit (S2 — Verified)

**Finding**: LangGraph checkpoints do NOT store `agent_id`. The `SessionState` class (`daemon/graph.py:327`) extends `MessagesState` and only adds `compacted_at: str | None`. Checkpoint serialization (`daemon/checkpoint_adapter.py`, `daemon/persistence.py`) contains zero references to `agent_id` or `agent_dir`. **No checkpoint migration needed.**

### Two-Layer Backward Compatibility Strategy

**Layer 1: Registry Alias (runtime)** — Handles ALL resolution methods:
- `resolve_pure_id("coder")` → `"developer"`
- `resolve_path_to_id("./agents/coder")` → `"developer"`
- `exists("coder")` → `True`
- Plus: `InstanceCreate` validator normalizes agent_id on API input

**Layer 2: DB Migration (one-time)** — Dual-engine:
- **PostgreSQL**: SQL statements added to `_ensure_postgres_columns()` in `daemon/manager.py`
- **SQLite**: SQL statements added to `run_migrations()` in `daemon/repositories/factory.py`
- Both update `agent_id` and `agent_dir` columns across all 6 tables

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `AGENT_ID_ALIASES` constant + alias resolution in ALL registry methods | Modify `resolve_pure_id()`, `resolve_path_to_id()`, `exists()` (S1) | `daemon/registry.py` |
| 2 | Add alias normalization in InstanceCreate validation | Normalize `agent_id="coder"` → `"developer"` at API input | `daemon/models/instance.py` |
| 3 | Add PostgreSQL migration statements | Add idempotent UPDATE statements to `_ensure_postgres_columns()` (C1 fix) | `daemon/manager.py` |
| 4 | Add SQLite migration statements | Add idempotent UPDATE statements to `run_migrations()` | `daemon/repositories/factory.py` |
| 5 | Create standalone migration script | CLI script for manual/offline migration | `scripts/migrate_coder_to_developer.py` |

## Key Files
- `daemon/registry.py` — Alias resolution logic (ALL methods)
- `daemon/models/instance.py` — Normalize agent_id on input
- `daemon/manager.py` — PostgreSQL migration (line 1587: `_ensure_postgres_columns()`)
- `daemon/repositories/factory.py` — SQLite migration (line 244: `run_migrations()`)
- `scripts/migrate_coder_to_developer.py` — New standalone migration script

---

## Detailed Design: Registry Alias (S1 — All Methods)

### daemon/registry.py

```python
# Add near SKIP_DIRS (line 18)
AGENT_ID_ALIASES: dict[str, str] = {
    "coder": "developer",
}
```

#### Modify `resolve_pure_id()` (line 229)

```python
def resolve_pure_id(self, agent_id: str) -> str | None:
    """Check if a string is a valid agent ID (with alias support)."""
    # Check for alias first (backward compat for renamed agents)
    canonical = AGENT_ID_ALIASES.get(agent_id, agent_id)
    if canonical in self._agents:
        return canonical
    # Also check the original in case alias maps to something not yet loaded
    if agent_id in self._agents:
        return agent_id
    return None
```

#### Modify `resolve_path_to_id()` (line 242) — S1 Fix

The current implementation at line 271 does a direct `if potential_id in self._agents:` check, **bypassing** alias resolution. Must route through `resolve_pure_id()`:

```python
def resolve_path_to_id(self, path_str: str) -> str | None:
    # ... (path normalization code unchanged) ...

    if agent_parts_idx >= 0:
        potential_id = parts[agent_parts_idx]
        # Route through resolve_pure_id() to handle aliases (S1 fix)
        return self.resolve_pure_id(potential_id)

    return None
```

#### Modify `exists()` (line 409) — S1 Fix

The current implementation does `return agent_id in self._agents`, **bypassing** alias resolution:

```python
def exists(self, agent_id: str) -> bool:
    """Check if agent exists (with alias support)."""
    return self.resolve_pure_id(agent_id) is not None
```

### daemon/models/instance.py — Input Normalization

```python
from pydantic import field_validator

# In InstanceCreate class
@field_validator("agent_id")
@classmethod
def normalize_agent_id(cls, v: str) -> str:
    """Normalize agent_id aliases (backward compat for renamed agents)."""
    from daemon.registry import AGENT_ID_ALIASES
    return AGENT_ID_ALIASES.get(v, v)
```

---

## Detailed Design: PostgreSQL Migration (C1 Fix)

### daemon/manager.py — `_ensure_postgres_columns()` (line 1587)

**CRITICAL**: Do NOT use `factory.py:run_migrations()` — it returns early for PostgreSQL (`if "sqlite" not in str(engine.url): return`, line 260). All PostgreSQL migrations go in `_ensure_postgres_columns()`.

Add the following statements to the `statements` list inside `_ensure_postgres_columns()`:

```python
# ── Agent rename: coder → developer ──────────────────────────────
# Idempotent UPDATE: renames agent_id and agent_dir from the old
# 'coder' agent to 'developer'. Safe to re-run (WHERE clause is a
# no-op if no rows match). Runs on every PostgreSQL startup.
#
# Tables: instances, instance_mappings, job_queue_items,
#         dead_letter_items, projects (creator_agent_id), jobqueue (legacy)
"UPDATE instances SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'",
"UPDATE instance_mappings SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'",
"UPDATE job_queue_items SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'",
"UPDATE dead_letter_items SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'",
"UPDATE projects SET creator_agent_id = 'developer' WHERE creator_agent_id = 'coder'",
# Legacy table (may not exist on fresh DBs — wrapped in try/except by the statement runner)
"UPDATE jobqueue SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'",
```

> **Note on `jobqueue` (legacy table)**: If this table doesn't exist, the UPDATE will raise an error. The `_ensure_postgres_columns()` method does NOT catch exceptions (by design — "fail loudly"). Options:
> - **Option A** (recommended): Add a guard checking table existence before the UPDATE.
> - **Option B**: Drop the `jobqueue` migration — this table is legacy and may not exist on any active deployment.
>
> **Recommended**: Option A — add a conditional:
> ```python
> "DO $$ BEGIN UPDATE jobqueue SET agent_id = 'developer' WHERE agent_id = 'coder'; EXCEPTION WHEN undefined_table THEN NULL; END $$",
> ```

---

## Detailed Design: SQLite Migration

### daemon/repositories/factory.py — `run_migrations()` (line 244)

Add a new migration block inside the `with engine.connect() as conn:` section, after the existing `_add_agent_id_column` calls (line ~312):

```python
# ── Agent rename: coder → developer ──────────────────────────────
# Idempotent UPDATE for SQLite. Safe to re-run.
# Uses SQLite's instr() to check for '/agents/coder' in agent_dir.
try:
    conn.execute(text("UPDATE instances SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'"))
    conn.execute(text("UPDATE instance_mappings SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'"))
    conn.execute(text("UPDATE job_queue_items SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'"))
    conn.execute(text("UPDATE dead_letter_items SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'"))
    conn.execute(text("UPDATE projects SET creator_agent_id = 'developer' WHERE creator_agent_id = 'coder'"))
    # Legacy table (may not exist)
    try:
        conn.execute(text("UPDATE jobqueue SET agent_id = 'developer', agent_dir = REPLACE(agent_dir, '/agents/coder', '/agents/developer') WHERE agent_id = 'coder'"))
    except Exception:
        pass  # Table doesn't exist, skip
    conn.commit()
    logger.info("Migration: Renamed agent_id 'coder' → 'developer' in all tables")
except Exception as e:
    logger.warning(f"Migration: coder→developer rename failed: {e}")
```

> SQLite supports `REPLACE(string, find, replacement)` natively, so the same SQL works.

---

## Detailed Design: Standalone Migration Script (S4 Clarification)

### scripts/migrate_coder_to_developer.py

A standalone CLI script for manual/offline migration. This is **not** the primary migration path (the auto-migration in `_ensure_postgres_columns()` / `run_migrations()` handles it at startup). This script is for:
- Pre-deployment testing (dry-run)
- Manual migration on a specific DB instance
- Verification that migration was applied

```python
#!/usr/bin/env python3
"""One-time migration: rename agent_id 'coder' → 'developer' in all tables.

Updates agent_id and agent_dir columns across all 6 tables. Supports both
PostgreSQL and SQLite (auto-detects from connection URL).

Tables:
    - instances (agent_id, agent_dir)
    - instance_mappings (agent_id, agent_dir)
    - job_queue_items (agent_id, agent_dir)
    - dead_letter_items (agent_id, agent_dir)
    - projects (creator_agent_id)
    - jobqueue (legacy, if exists)

Usage:
    python scripts/migrate_coder_to_developer.py [--dry-run] [--db-url URL]
    python scripts/migrate_coder_to_developer.py --dry-run
    python scripts/migrate_coder_to_developer.py --db-url postgresql://user:pass@localhost/db
"""
```

The script auto-detects the DB engine from the URL and executes the same UPDATE statements as the auto-migration. It includes a `--dry-run` mode that shows affected row counts without modifying.

---

## Risks

| Risk | Mitigation |
|------|------------|
| PostgreSQL migration silently no-ops (C1) | Migration added to `_ensure_postgres_columns()`, NOT `run_migrations()` |
| `jobqueue` legacy table doesn't exist | Wrapped in exception handler (PostgreSQL: `DO $$ ... EXCEPTION`, SQLite: try/except) |
| `dead_letter_items` has "coder" rows (C3) | Included in migration |
| `exists()` and `resolve_path_to_id()` bypass alias (S1) | All 3 methods route through alias logic |
| Checkpoints store stale agent_id (S2) | Verified: checkpoints do NOT store agent_id — no action needed |
| `_migrate_agent_id_rename` undefined (S4) | Not used — migration is inline SQL in both engines |

## Constraints
- PostgreSQL migration MUST go in `_ensure_postgres_columns()` (per critical note: .sql migrations and `run_migrations()` NO-OP on PostgreSQL)
- SQLite migration goes in `run_migrations()` (SQLite-only function)
- All migration statements must be idempotent (WHERE clause = no-op if no matches)
- Must support both PostgreSQL AND SQLite
- `REPLACE()` function syntax is identical for both engines

## Deliverables
- [ ] `AGENT_ID_ALIASES` dict in `daemon/registry.py` maps `"coder"` → `"developer"`
- [ ] `resolve_pure_id("coder")` returns `"developer"`
- [ ] `resolve_path_to_id("./agents/coder")` returns `"developer"` (S1 fix)
- [ ] `exists("coder")` returns `True` (S1 fix)
- [ ] `InstanceCreate(agent_id="coder")` normalizes to `"developer"`
- [ ] PostgreSQL migration statements in `_ensure_postgres_columns()` cover all 6 tables
- [ ] SQLite migration statements in `run_migrations()` cover all 6 tables
- [ ] `scripts/migrate_coder_to_developer.py` exists with `--dry-run` support
- [ ] Migration is idempotent (safe to run multiple times)
