# Phase 1: Backend — Built-in MCP Server Framework

## Objective
Extend the backend MCP infrastructure to support built-in servers: add `is_builtin`, `config_schema`, and `config_schema_version` fields to the DB model, create a built-in server registry with abstract base class (including `build_config` + `parse_config`), add API protection, add new endpoints for templates/configure/reset, implement fault-tolerant auto-seeding on daemon startup, and add comprehensive tests.

**Key architecture rule**: Repository is a pure DB layer — no imports from registry or `BuiltinServerDefinition`. All business logic (config generation, reverse-mapping, validation) lives in the router/manager layer, which orchestrates via the registry.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: —
- **Shared APIs/interfaces**: New endpoints, new fields in responses, `BuiltinServerDefinition` abstract class

---

## Tasks

### 1. DB Migration — Add built-in fields to `mcp_servers`
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1.1 | Create migration file | `20260517_000001_add_builtin_fields_to_mcp_servers.sql` | `daemon/migrations/versions/` |
| 1.2 | UP section | `ALTER TABLE mcp_servers ADD COLUMN is_builtin BOOLEAN DEFAULT 0; ALTER TABLE mcp_servers ADD COLUMN config_schema JSON; ALTER TABLE mcp_servers ADD COLUMN config_schema_version VARCHAR DEFAULT '0';` | same |
| 1.3 | DOWN section | `ALTER TABLE mcp_servers DROP COLUMN config_schema_version; ALTER TABLE mcp_servers DROP COLUMN config_schema; ALTER TABLE mcp_servers DROP COLUMN is_builtin;` (SQLite ≥ 3.35.0 supports DROP COLUMN) | same |

### 2. SQLModel — Update DB Model
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 2.1 | Add `is_builtin` | `is_builtin: bool = Field(default=False)` | `daemon/repositories/mcp_server/models.py` |
| 2.2 | Add `config_schema` | `config_schema: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))` — JSON array of serialized `ConfigSchemaField` dicts | same |
| 2.3 | Add `config_schema_version` | `config_schema_version: str = Field(default="0")` | same |

### 3. Pydantic API Models
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 3.1 | Create `ConfigSchemaField` model | `key: str`, `label: str`, `type: Literal["text", "number", "boolean", "select"]`, `description: str | None`, `default: Any | None`, `required: bool = True`, `options: list[str] | None`, `min: float | None`, `max: float | None`, `section: Literal["args", "env"]`, `arg_format: Literal["key_value", "flag"] = "key_value"` | `daemon/models/mcp_server.py` |
| 3.2 | Update `McpServerInfo` | Add `is_builtin: bool = Field(default=False)`, `config_schema: list[ConfigSchemaField] | None = Field(default=None)`, `config_schema_version: str = Field(default="0")`, `initial_values: dict[str, Any] | None = Field(default=None)` — reverse-mapped from stored config for frontend form pre-fill | same |
| 3.3 | Create `BuiltinServerTemplate` model | `name: str`, `description: str`, `config_schema: list[ConfigSchemaField]`, `default_config: dict[str, Any]` | same |
| 3.4 | Create `BuiltinTemplateListResponse` | `templates: list[BuiltinServerTemplate]` | same |
| 3.5 | Create `BuiltinServerConfigure` request | `template_name: str`, `values: dict[str, Any]` | same |
| 3.6 | Add `BUILTIN_SERVER_PROTECTED` error code | `daemon/models/common.py`: `BUILTIN_SERVER_PROTECTED = "BUILTIN_SERVER_PROTECTED"` | `daemon/models/common.py` |

### 4. Built-in Server Registry + Abstract Base Class
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 4.1 | Create registry module | `daemon/mcp/builtin_servers/__init__.py` with `BuiltinServerRegistry` class | `daemon/mcp/builtin_servers/__init__.py` |
| 4.2 | Define `BuiltinServerDefinition` ABC | Abstract properties: `name -> str`, `description -> str`, `schema_version -> str`. Abstract methods: `get_config_schema() -> list[ConfigSchemaField]`, `get_base_config() -> dict[str, Any]`, `build_config(user_values) -> dict[str, Any]`, `parse_config(stored_config) -> dict[str, Any]`. | `daemon/mcp/builtin_servers/base.py` |
| 4.3 | Implement `build_config` (generic, overridable) | Iterates schema fields: `arg_format="key_value"` → `["--key-name", str(value)]`, `arg_format="flag"` → `["--flag-name"]` if True / omit if False, `section="env"` → `{"KEY": str(value)}`. Merges extra args/env with `get_base_config()`. | same |
| 4.4 | Implement `parse_config` (generic, overridable) | Reverse-maps stored MCP config → `{ key: value }` dict for form pre-fill. Parses `args` list: walks from end of base args, extracts `--key-name value` pairs and `--flag-name` flags. Matches against schema to resolve `arg_format` and type coercion (boolean flags, numbers). Returns `dict[str, Any]`. | same |
| 4.5 | Implement registry singleton | `register(definition)`, `get_all() -> list[BuiltinServerDefinition]`, `get_by_name(name) -> BuiltinServerDefinition | None` | `daemon/mcp/builtin_servers/__init__.py` |

### 5. Validation Helper
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 5.1 | Create `validate_config_values` | Standalone function: takes `list[ConfigSchemaField]` + `dict[str, Any]` values. Checks: required fields present, type matches (text→str, number→int/float, boolean→bool, select→one of options), number min/max bounds. Raises `McpConfigValidationError` with field-specific messages. | `daemon/mcp/builtin_servers/validation.py` |

### 6. Repository Extensions (pure DB layer — no registry imports)
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 6.1 | Update `create_mcp_server` | Add params: `is_builtin: bool = False`, `config_schema: list[dict] | None = None`, `config_schema_version: str = "0"`. Pure DB operation. | `daemon/repositories/mcp_server/repository.py` |
| 6.2 | Update `list_mcp_servers` | Add `is_builtin: bool | None = None` filter. | same |
| 6.3 | Add `get_mcp_server_by_name_and_builtin` | `get_mcp_server_by_name(name)` already exists — just use it in router logic; no new repo method needed. | — |
| 6.4 | Add `update_mcp_server` note | Existing `update_mcp_server` already handles partial updates — used as-is for reset flow (router passes pre-built config dict). | same |

### 7. API Router — Protection + New Endpoints
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 7.1 | Add registry helper | `_get_registry() -> BuiltinServerRegistry` — accesses registry singleton for router use. | `daemon/routers/mcp_servers.py` |
| 7.2 | Update `_mcp_server_to_info` | Include `is_builtin`, `config_schema`, `config_schema_version`. For built-in servers: call `definition.parse_config(server.config)` via registry → populate `initial_values` field. | same |
| 7.3 | Delete protection | `delete_mcp_server`: if `existing.is_builtin` → 403 with `BUILTIN_SERVER_PROTECTED`. | same |
| 7.4 | Update protection | `update_mcp_server`: if `existing.is_builtin` → reject `name`/`description` changes with 403; allow `config`/`is_active` only. | same |
| 7.5 | `GET /api/mcp-servers/builtin-templates` | Returns `BuiltinTemplateListResponse`. Calls `registry.get_all()`, converts each to `BuiltinServerTemplate`. | same |
| 7.6 | `POST /api/mcp-servers/configure-builtin` | Takes `BuiltinServerConfigure`. Looks up definition by name in registry. Calls `validate_config_values(schema, values)`. Calls `definition.build_config(values)`. Then: if server exists in DB and `is_builtin=True` → `update_mcp_server(config=generated_config)`. If not exists → `create_mcp_server(name=..., config=generated_config, is_builtin=True, config_schema=..., config_schema_version=...)`. If exists and `is_builtin=False` → 409 conflict. Returns `McpServerInfo`. | same |
| 7.7 | `POST /api/mcp-servers/{server_id}/reset-builtin` | Validates `is_builtin=True`. Looks up definition via registry. Calls `definition.build_config({})`. Calls `update_mcp_server(server_id, config=defaults_config)`. Returns `McpServerInfo`. | same |

### 8. Daemon Bootstrap — Fault-Tolerant Auto-Seed
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 8.1 | Add `_bootstrap_builtin_servers()` | For each definition in registry: (1) call `definition.build_config({})` for default config, (2) check if server exists by name, (3a) if not exists → `create_mcp_server(name, is_builtin=True, config_schema=..., config_schema_version=..., config=default_config)`, (3b) if exists and `is_builtin=True` and `config_schema_version != definition.schema_version` → `update_mcp_server(config_schema=..., config_schema_version=...)` (preserve user config), (3c) if exists and `is_builtin=False` → log warning, skip. All wrapped in per-server try/except. | `daemon/manager.py` |
| 8.2 | Call bootstrap | After repository init (line ~424), before service init. | same |
| 8.3 | Register built-in servers | Import and register all definitions in `daemon/mcp/builtin_servers/__init__.py` at module level. | same |

### 9. Testing
| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 9.1 | Test `build_config` generic | Verify key_value args, boolean flags (True→emit, False→omit), env vars, None omission | `tests/` |
| 9.2 | Test `parse_config` generic | Verify reverse-mapping: key_value extraction, flag detection (presence=True), type coercion | `tests/` |
| 9.3 | Test `validate_config_values` | Required missing → error, type mismatch → error, number out of range → error, valid → pass | `tests/` |
| 9.4 | Test delete protection (403) | Built-in server → 403, user server → 200 | `tests/` |
| 9.5 | Test update protection (403) | Built-in: reject name/description changes, allow config/is_active | `tests/` |
| 9.6 | Test `/builtin-templates` | Returns all registered templates | `tests/` |
| 9.7 | Test `/configure-builtin` | Validation, config generation, DB creation, conflict with user server → 409 | `tests/` |
| 9.8 | Test `/reset-builtin` | Resets to defaults, non-built-in → 403 | `tests/` |
| 9.9 | Test bootstrap idempotency | Run twice → no duplicates, no corruption | `tests/` |
| 9.10 | Test bootstrap fault tolerance | One server fails → others still created, daemon starts | `tests/` |
| 9.11 | Test `_mcp_server_to_info` with initial_values | Built-in server → response includes `initial_values` from `parse_config` | `tests/` |

---

## Key Files
- `daemon/migrations/versions/20260517_000001_add_builtin_fields_to_mcp_servers.sql` — Migration
- `daemon/repositories/mcp_server/models.py` — DB model (3 new fields)
- `daemon/repositories/mcp_server/repository.py` — Repository extensions (pure DB)
- `daemon/models/mcp_server.py` — Pydantic models (ConfigSchemaField, templates, configure, initial_values)
- `daemon/models/common.py` — `BUILTIN_SERVER_PROTECTED` error code
- `daemon/routers/mcp_servers.py` — API protection + 3 new endpoints + registry orchestration
- `daemon/mcp/builtin_servers/__init__.py` — Registry
- `daemon/mcp/builtin_servers/base.py` — ABC with `build_config` + `parse_config`
- `daemon/mcp/builtin_servers/validation.py` — `validate_config_values` helper
- `daemon/manager.py` — Bootstrap

## Layering Diagram

```
┌──────────────────────────────────────┐
│  Router / Manager (orchestration)    │  ← calls registry, validates, generates config
│  - configure-builtin endpoint        │
│  - reset-builtin endpoint            │
│  - _bootstrap_builtin_servers()      │
│  - _mcp_server_to_info()             │
├──────────────────────────────────────┤
│  Registry + BuiltinServerDefinition  │  ← build_config(), parse_config(), schemas
│  - validate_config_values()          │
├──────────────────────────────────────┤
│  Repository (pure DB)                │  ← create/update/list/delete, NO registry imports
│  - is_builtin filter param           │
│  - config_schema storage             │
└──────────────────────────────────────┘
```

## Constraints
- **Repository is pure DB**: No registry or definition imports in repository layer
- **Backward compatible**: Existing servers work unchanged
- **Additive only**: New fields have defaults
- **Idempotent bootstrap**: Safe to run multiple times
- **Fault tolerant**: Per-server try/except in bootstrap

## Deliverables
- [ ] Migration adding `is_builtin`, `config_schema`, `config_schema_version`
- [ ] Updated DB model with 3 new fields
- [ ] `ConfigSchemaField` with `arg_format` support
- [ ] `BUILTIN_SERVER_PROTECTED` error code
- [ ] `BuiltinServerDefinition` ABC with `build_config()` + `parse_config()` + `get_base_config()`
- [ ] `validate_config_values()` helper
- [ ] Registry singleton
- [ ] Repository extensions (pure DB, no registry dependency)
- [ ] API protection (403 for built-in delete/field-update)
- [ ] 3 new endpoints: `/builtin-templates`, `/configure-builtin`, `/{id}/reset-builtin`
- [ ] `initial_values` in `McpServerInfo` response (reverse-mapped for frontend)
- [ ] Fault-tolerant bootstrap with schema drift detection
- [ ] 11 test cases
