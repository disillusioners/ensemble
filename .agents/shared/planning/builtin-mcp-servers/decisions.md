# Key Design Decisions: Built-in MCP Servers

## Decision 1: `is_builtin` boolean flag vs `category` enum

**Chosen: `is_builtin` boolean flag**

| Option | Pros | Cons |
|--------|------|------|
| `is_builtin: bool` | Simple, clear intent, easy to query | Less extensible |
| `category: str` | More extensible | Over-engineering |

**Rationale**: Binary distinction is all we need. Migration to enum later is cheap.

---

## Decision 2: Config schema stored in DB with version tracking

**Chosen: Store `config_schema` + `config_schema_version` in DB**

**Rationale**: Frontend gets schema in the standard `McpServerInfo` response — no extra API call needed. Registry is source of truth; bootstrap syncs to DB. Version field enables drift detection so existing installations auto-update stale schemas.

---

## Decision 3: Separate `/configure-builtin` endpoint

**Chosen: Dedicated endpoint**

**Rationale**: Built-in configure flow is fundamentally different from generic update — takes user-friendly values, generates config, validates against schema, upserts. Keeps generic CRUD clean.

---

## Decision 4: Generic `build_config` with `arg_format` discrimination

**Chosen: Generic algorithm in base class with `arg_format` field controlling behavior**

### Algorithm
```python
def build_config(self, user_values: dict[str, Any]) -> dict[str, Any]:
    schema = self.get_config_schema()
    base = self.get_base_config()
    extra_args, env_vars = [], {}

    for field in schema:
        value = user_values.get(field.key, field.default)
        if value is None:
            continue

        if field.section == "args":
            if field.arg_format == "flag":
                if value is True:
                    extra_args.append("--" + field.key.replace("_", "-"))
            else:  # key_value
                extra_args.extend(["--" + field.key.replace("_", "-"), str(value)])
        elif field.section == "env":
            env_vars[field.key.upper()] = str(value)

    config = {**base}
    config["args"] = base.get("args", []) + extra_args
    if env_vars:
        config["env"] = env_vars
    return config
```

| `arg_format` | `section` | True/Value | False/None |
|---|---|---|---|
| `"key_value"` | `"args"` | `["--key-name", "value"]` | Omit |
| `"flag"` | `"args"` | `["--flag-name"]` (no value) | Omit |
| (any) | `"env"` | `{"KEY_NAME": "value"}` | Omit |

---

## Decision 5: `parse_config` for reverse-mapping stored config → form values

**Chosen: Server-specific `parse_config` method on `BuiltinServerDefinition`**

### Why needed
`build_config` is lossy: `{ user_agent: "X", ignore_robots_txt: true }` → `{ args: ["--user-agent", "X", "--ignore-robots-txt"] }`. You cannot recover the original dict from the generated args without knowing the schema. The reverse mapping requires knowing which args are key_value pairs, which are flags, and their types.

### Algorithm
```python
def parse_config(self, stored_config: dict[str, Any]) -> dict[str, Any]:
    schema = self.get_config_schema()
    schema_by_flag = {
        "--" + f.key.replace("_", "-"): f for f in schema if f.section == "args"
    }
    result = {}
    args = stored_config.get("args", [])
    base_args_count = len(self.get_base_config().get("args", []))
    user_args = args[base_args_count:]  # Skip base args

    i = 0
    while i < len(user_args):
        arg = user_args[i]
        if arg in schema_by_flag:
            field = schema_by_flag[arg]
            if field.arg_format == "flag":
                result[field.key] = True
                i += 1
            else:  # key_value
                result[field.key] = _coerce(field.type, user_args[i + 1])
                i += 2
        else:
            i += 1  # Unknown arg, skip

    # Fill defaults for missing fields
    for field in schema:
        if field.key not in result and field.default is not None:
            result[field.key] = field.default

    return result
```

**Rationale**: Generic implementation works for any stdio server that follows the `--key value` / `--flag` pattern. Servers with unusual arg formats can override.

---

## Decision 6: Repository is pure DB layer — no registry imports

**Chosen: Strict layering — repository never imports registry or `BuiltinServerDefinition`**

### Layering diagram
```
Router/Manager  →  Registry (build_config, parse_config, validate)
       ↓
Repository      →  Pure DB (create, update, list, delete)
```

### Flow for `/configure-builtin`
```
Router:
  1. registry.get_by_name(template_name)  → definition
  2. validate_config_values(schema, values)
  3. definition.build_config(values)       → config dict
  4. repo.get_mcp_server_by_name(name)     → check exists
  5. repo.create_mcp_server(..., config=config_dict, is_builtin=True)
     OR repo.update_mcp_server(id, config=config_dict)
```

### Flow for `/reset-builtin`
```
Router:
  1. repo.get_mcp_server(id)               → check is_builtin
  2. registry.get_by_name(server.name)      → definition
  3. definition.build_config({})            → defaults config dict
  4. repo.update_mcp_server(id, config=defaults_config)
```

### Flow for `_mcp_server_to_info` (built-in)
```
Router helper:
  1. registry.get_by_name(server.name)      → definition
  2. definition.parse_config(server.config) → initial_values dict
  3. McpServerInfo(..., initial_values=initial_values)
```

**Rationale**: Repository should only depend on DB models. All business logic (config generation, reverse-mapping, validation) lives in the orchestration layer. This keeps the repository testable in isolation and avoids circular dependencies.

---

## Decision 7: Eager, fault-tolerant bootstrap

**Chosen: Bootstrap at daemon startup, per-server try/except**

**Rationale**: Negligible cost for 1-2 servers. Each server seeded independently — one failure doesn't block others. Daemon starts regardless.

---

## Decision 8: User server wins on name conflict

**Chosen**: If user has a server with the same name, skip seeding. Log warning.

**Frontend shows**: `"Delete or rename your custom server to enable the built-in version."`

**Rationale**: Never break user data. Name uniqueness constraint means we can't coexist. User is in control.

---

## Decision 9: `reset-builtin` endpoint

**Chosen: `POST /{id}/reset-builtin`** — router calls `build_config({})` via registry, passes result to repo.

**State sync**: Response returns updated server with new `initial_values`. Frontend updates local signal and re-initializes form.

---

## Decision 10: `BUILTIN_SERVER_PROTECTED` error code

**Chosen**: Specific error code, not generic 403. Frontend can distinguish and show targeted messaging.

---

## Decision 11: `arg_format` is server-side only — not in frontend TypeScript

**Chosen**: `arg_format` is NOT included in the frontend `ConfigSchemaField` interface.

**Rationale**: `arg_format` controls how `build_config` generates CLI args — this is purely a backend concern. The frontend only needs `key`, `label`, `type`, `description`, `default`, `required`, `options`, `min`, `max`, and `section` to render the form and submit values. The backend's `/configure-builtin` endpoint receives plain `{ key: value }` pairs and handles arg formatting internally.

---

## Decision 12: `config_schema_version` is server-side only — not in frontend

**Chosen**: `config_schema_version` is not exposed to or used by the frontend.

**Rationale**: Schema versioning is a backend bootstrap concern — it detects drift and updates stored schemas on startup. The frontend always gets the latest schema from the API response and doesn't need to know about versioning.

---

## Summary Table

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | DB field type | `is_builtin: bool` | Simplicity; YAGNI |
| 2 | Schema storage | In DB with version tracking | Single API call for all data |
| 3 | API design | Separate `/configure-builtin` | Distinct flow from generic CRUD |
| 4 | Config builder | Generic with `arg_format` | Handles key_value, flags, env |
| 5 | Reverse mapping | `parse_config` on definition | Lossy generation requires schema-aware reversal |
| 6 | Repository layering | Pure DB — no registry imports | Clean architecture, testable |
| 7 | Bootstrap | Eager, fault-tolerant | Per-server try/except |
| 8 | Name conflicts | User server wins | Never break user data |
| 9 | Reset | Dedicated endpoint | Safe recovery without delete |
| 10 | Error code | `BUILTIN_SERVER_PROTECTED` | Specific, disambiguable |
| 11 | `arg_format` in frontend | Omitted | Server-side concern only |
| 12 | `config_schema_version` in frontend | Omitted | Backend bootstrap concern only |
