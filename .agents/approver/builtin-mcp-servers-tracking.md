# Plan Approval Tracking: Built-in MCP Servers

## Iteration 001

- **Date**: 2026-05-18
- **Verdict**: APPROVED
- **Evaluator**: Approver (independent)

### Evaluation Summary

Plan evaluated against current codebase (verified all referenced files exist and match described architecture).

**Verification performed:**
- DB model (`McpServer` SQLModel) — confirmed no `is_builtin`/`config_schema`/`config_schema_version` fields exist
- Repository — confirmed existing method signatures match plan's expectations
- Router — confirmed 5 endpoints, `_mcp_server_to_info` helper, manager access pattern
- API models — confirmed `McpServerInfo` fields, `ErrorCodes` enum (no `BUILTIN_SERVER_PROTECTED`)
- Migrations — confirmed timestamp-based naming pattern

**Council session**: 1 sequential session covering architecture, roundtrip, bootstrap, API, migration, frontend contract, missing concerns.

### Council Findings (all non-blocking)

The council raised 4 "blocking" issues. Independent assessment:

1. **`default_value` missing from ConfigSchemaField** — NOT a gap. Plan specifies `default: Any | None` which serves this purpose.
2. **`input_type` not exposed to frontend** — NOT a gap. `type: Literal["text", "number", "boolean", "select"]` is sufficient for form rendering. `arg_format` is correctly identified as server-side only.
3. **`initial_values` algorithm unspecified** — Already specified in Decision 5 (`parse_config`) and Phase 1 task 7.2.
4. **`parse_config` edge case bounds** — Valid concern but not blocking. Documented `--key value` / `--flag` pattern is unambiguous for the webfetch fields.

### Notes for implementation
- Add a roundtrip property test: `parse_config(build_config(values)) == values`
- Document that config values must not contain `--` prefix or be empty strings
- Ensure bootstrap ordering: after repo init, before service init
