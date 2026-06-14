# Plan Overview: Database Tool Category (`db`)

## Objective
Create a new `db` tool category that gives agents (coder, devops, tester) the ability to manage named external database connections and execute read-only PostgreSQL SELECT queries against them. This is an agent-facing tool — **NOT** related to the ensemble's internal database.

## Scope Assessment
**BIG** — New SQLModel table + repository (dual SQLite/PostgreSQL), encrypted credential storage, manager-level connection pool service (singleton), 5 new tool functions, tool-category registration, agent `meta.json` updates, lifecycle cleanup wiring, and test coverage. Touches ~15 files (7 new, 8 modified).

## Key Architecture Decisions

### D1: Reuse `CredentialManager` — Encryption in TOOL Layer, Not Repository (N1)
The codebase already has `daemon/sources/credentials.py::CredentialManager` using `cryptography.fernet.Fernet` for symmetric encryption. **We reuse this class** — but encryption/decryption happens in the **tool functions**, NOT the repository. This matches the actual codebase pattern (`source_configs` where `routers/sources.py` handles encryption, not the repository). `DbConnectionRepository.__init__(self, engine: Engine)` — NO credential_manager parameter. See D1 in decisions.md for full detail.

### D2: Single `db_conn` Table — Encrypted Credentials Column
The connection registry table (`db_connections`) stores all connection metadata in columns (name, db_type, host, port, username) and stores the **password as an encrypted string** in a `credentials` column — exactly the pattern used by `source_configs.credentials`. This keeps the model simple and consistent with existing patterns.

### D3: Connection Pooling via `asyncpg.create_pool`
For the SELECT execution tool, we use `asyncpg` (already installed) with per-connection-name connection pools. A `ConnectionPoolManager` service caches `asyncpg.Pool` instances keyed by connection name. Pools are created lazily on first query and disposed on connection deletion. **No SQLAlchemy async engine** — asyncpg is purpose-built for fast PostgreSQL queries and is already a dependency.

### D4: SELECT-Only Enforcement via Keyword Analysis (lightweight, no dep)
Rather than pulling in `sqlparse` as a dependency, we use a **multi-layer guard**: (1) strip comments and whitespace, (2) check the first SQL keyword is `SELECT` or `WITH` (for CTEs), (3) **strip string literals** (N4: replace `'...'` with `''` to prevent false positives on keywords inside string values), (4) reject if any forbidden DML/DDL keywords (`INSERT`, `INTO`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `GRANT`, `REVOKE`, etc.) appear, checked via word-boundary regex. `INTO` is included to block `SELECT ... INTO` (DDL side-effect). This is a pragmatic guard — not a full SQL parser — and is documented as defense-in-depth, not a security boundary (the true boundary is DB-level read-only roles).

### D5: Shared Services at Manager Level + Factory-Closure Tools
DB tools are created by a `create_db_tools(manager, instance_id, repository, pool_manager)` factory function. The `DbConnectionRepository` and `ConnectionPoolManager` are **created once at the `InstanceManager` level** — NOT per-instance. The `CredentialManager` is **injected from `app.state`** (N5). This prevents pool proliferation. See D5 in decisions.md for full detail.

### D6: Tool Naming — `db_*` Category
Per the requirement: `db` category registered in `CATEGORY_MODULES`. Within it:
- **Connection management:** `db_conn_add`, `db_conn_delete`, `db_conn_list`, `db_conn_test`
- **PostgreSQL DML:** `db_postgres_dml_select`

All registered under category `"db"` via `@register_tool_category("db")`.

### D7: Sync Repository Calls in Async Context (C2)
Connection CRUD operations (low-frequency admin ops) use sync `Session(engine)` inside async tool functions. Acceptable because CRUD is not a hot path; the query execution path (asyncpg) is fully async. Escape hatch: `asyncio.to_thread()` if needed. See D7 in decisions.md.

### D8: Inject Existing `CredentialManager` from `app.state` (N5)
The existing `CredentialManager` at `app.state.credential_manager` (created at `api.py:232`) is injected into `InstanceManager` via constructor param. NO new instance created. See D8 in decisions.md.

## Context
- **Project:** agents-ensemble
- **Working Directory:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Python:** ≥3.13
- **Confirmed dependencies (already in `pyproject.toml`):** `cryptography>=42.0.0`, `psycopg[binary]>=3.1.0`, `asyncpg>=0.29.0`, `sqlmodel>=0.0.22`, `aiosqlite>=0.20.0`
- **Tool registration:** `@register_tool_category("db")` + `@tool` decorator, assembled in `create_instance_tools()` at `daemon/tools/instance.py:439`
- **CATEGORY_MODULES registry:** `daemon/tools/_tool_registry.py:184` — must add `"db"` entry
- **Manager engine access:** `manager.engine` property at `daemon/manager.py:983`
- **Manager constructor:** `InstanceManager(config, ensemble_config)` at `manager.py:440` — add `credential_manager` param (N5)
- **Manager shutdown steps:** `shutdown()` at `manager.py:2825` — add `dispose_db_pools` step (C6, N2)
- **Manager cleanup:** `cleanup()` at `manager.py:2786` is SYNC — do NOT modify (N2)
- **Existing CredentialManager:** `app.state.credential_manager` at `api.py:232` — inject, don't duplicate (N5)
- **CredentialManager class:** `daemon/sources/credentials.py` — Fernet-based encrypt/decrypt
- **Source repository pattern (reference):** `daemon/repositories/source/` — repository has NO credential_manager; encryption in router/tool layer (N1)
- **Agent meta.json allow lists:** `agents/coder/meta.json`, `agents/devops/meta.json`, `agents/tester/meta.json` — currently allow `["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context"]`
- **Dual-DB requirement:** The `db_connections` table is created via `SQLModel.metadata.create_all(engine)` — works identically on SQLite and PostgreSQL
- **W5/N10 — Model import:** `DbConnectionConfig` imported at MODULE LEVEL of `manager.py` BEFORE `SQLModel.metadata.create_all(engine)`

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Connection Registry Layer | SQLModel table + repository with encrypted credentials + model import in manager.py | None | — | 3h |
| 2 | Connection Pool Service + Manager Wiring | `ConnectionPoolManager` (asyncpg) + manager-level singleton wiring (C3) + lifecycle cleanup (C6) | Phase 1 | loose | 3h |
| 3 | DB Tools Implementation | 5 tool functions + SELECT guard (with INTO) + registration + wiring | Phase 1, 2 | tight | 3h |
| 4 | Agent Access + Tests | Update 3 meta.json files + comprehensive test suite (incl. INTO, W1, C3) | Phase 3 | loose | 2.5h |

**Total Estimated Time:** ~11.5 hours

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| Phase 1 → Phase 2 | **loose** | Phase 2 needs the `DbConnectionConfig` model from Phase 1 but only for type reference (reads connection params). Can pipeline after Phase 1 review. |
| Phase 2 → Phase 3 | **tight** | Phase 3's `db_conn_test` and `db_postgres_dml_select` directly call `ConnectionPoolManager` methods. Must wait for Phase 2. |
| Phase 3 → Phase 4 | **loose** | Phase 4 adds `"db"` to meta.json allow-lists and writes tests that import the tools. Can pipeline (tests written while Phase 3 is reviewed). |

### Phase Scheduling Diagram

```
Phase 1 (Registry Layer)
    │
    ├──→ Phase 2 (Pool Service) [loose — pipeline after P1 review]
    │        │
    │        └──→ Phase 3 (Tools) [tight — wait for P2]
    │                 │
    │                 └──→ Phase 4 (Agent Access + Tests) [loose — pipeline]
    │
    └── [Phase 4 can start writing meta.json changes in parallel with P3 review]
```

**Recommended execution:** Sequential Phases 1→2→3, then Phase 4 can overlap with Phase 3 review.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Credential key not configured (`SOURCE_CREDENTIAL_KEY` unset) → passwords stored as plaintext JSON | **high** | Reuse existing `CredentialManager` behavior — logs warning. Document that operators must set `SOURCE_CREDENTIAL_KEY`. Consistent with existing source credentials. |
| Pool proliferation (N instances × M connections) | **high** | **C3 fix:** Pool manager is singleton at `InstanceManager` level. All instances share one pool per connection name. |
| SELECT-only guard bypassed by sophisticated SQL (e.g., `SELECT ... INTO`, `WITH x AS (DELETE ... RETURNING)`) | **med** | Multi-layer check: first keyword must be SELECT/WITH, strip string literals (N4), scan for forbidden keywords (incl. `INTO`). Document as defense-in-depth (W3/W4/W6). DB-level read-only roles are the true boundary. |
| Connection pool leak on daemon shutdown | **med** | **C6/N2 fix:** `dispose_all()` added to `shutdown()` steps list (NOT `cleanup()` which is sync, NOT `api.py` lifespan which would double-dispose). |
| DSN/password leaked in error messages | **high** | **W1/N9 fix:** `_sanitize_error()` extracts first line, redacts DSN-like patterns and `password=...` via regex deny-list. |
| DSN drops username when password is None (.pgpass/peer/IAM auth) | **high** | **N3 fix:** Three-case DSN building: user+password, user-only, anonymous. |
| String-literal false positives in SELECT guard | **med** | **N4 fix:** Strip single-quoted string literals before keyword scan. `SELECT * FROM t WHERE v = '%INTO%'` no longer falsely rejected. |
| Duplicate `CredentialManager` instance | **low** | **N5 fix:** Inject existing `app.state.credential_manager` via constructor. No new instance. |
| Query timeout not enforced | **med** | **W2 fix:** `execute_select()` uses `asyncio.wait_for(conn.fetch(query), timeout=timeout)`. |
| Table not created on startup (SQLModel metadata gap) | **high** | **W5/N10 fix:** `DbConnectionConfig` imported at MODULE LEVEL of `manager.py` before `SQLModel.metadata.create_all()`. |
| Prepared statement cache churn for ad-hoc queries | **low** | **N7 fix:** Use `conn.fetch(query)` directly instead of `conn.prepare(query).fetch()`. |
| asyncpg type codec issues with custom PostgreSQL types | **low** | Default to `asyncpg.connect` with standard codecs. Not a concern for typical SELECT queries. |
| Large result sets causing memory issues | **med** | Enforce `MAX_ROWS` limit (default 1000) on SELECT results. Truncate with a clear message if exceeded. |

## Success Criteria
- [ ] `db_conn_add` encrypts password in the TOOL layer and stores opaque string in repository (N1)
- [ ] `db_conn_list` returns all connections WITHOUT exposing passwords
- [ ] `db_conn_delete` removes connection and disposes its pool
- [ ] `db_conn_test` validates connectivity and returns clear pass/fail
- [ ] `db_postgres_dml_select` executes SELECT and returns formatted results
- [ ] `SELECT ... INTO` is rejected (C1)
- [ ] Non-SELECT queries (INSERT, UPDATE, DELETE, DROP, etc.) are rejected with clear error
- [ ] Keywords inside string literals don't trigger false positives (N4)
- [ ] Passwords never appear in any tool output or log message
- [ ] Error messages never include DSN/connection strings (W1, N9)
- [ ] DSN building preserves username when password is None (N3)
- [ ] Query timeout is enforced via `asyncio.wait_for()` (W2)
- [ ] Connection registry table works on both SQLite and PostgreSQL
- [ ] `db_connections` table auto-creates on startup (W5/N10 — model imported at module level)
- [ ] Pool manager is singleton at manager level — all instances share one pool per connection (C3)
- [ ] `CredentialManager` injected from `app.state`, not duplicated (N5, D8)
- [ ] Pool cleanup in `shutdown()` steps, NOT `cleanup()` or `api.py` lifespan (C6, N2, N6)
- [ ] `db` category appears in tool registry and help system
- [ ] coder, devops, tester agents have `db` in their `tools.allow`
- [ ] Test suite covers: connection CRUD, encryption round-trip, SELECT execution, INTO rejection, string-literal false positives, three-case DSN, rejection of non-SELECT, pool lifecycle, error sanitization, tool filtering

## Known Limitations (Deferred — W3/W4/W6, W8)
- **W3/W4/W6:** SELECT guard is defense-in-depth, not a security boundary. DB-level read-only roles are the true protection. Documented in decisions.md (D4) and phase tests.
- **W8:** `db_conn_update` tool is out of scope for v1. Connections can be deleted and re-added if params change.

## Tracking
- Created: 2025-06-25
- Last Updated: 2025-06-25
- Revision: v4 — Review fixes (BLOCKER 1: api.py reorder, BLOCKER 2: @property credential_manager, BLOCKER 3: PG native quoted-format error sanitization)
- Status: draft
