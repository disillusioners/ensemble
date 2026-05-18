# Built-in MCP Servers Testing Lessons

## Date: 2026-05-18

### Key Findings

1. **mcp module dependency**: The `mcp` Python package must be installed for tests to collect. Without it, 14 test files fail at collection phase. Use `uv sync` or `pip install mcp`.

2. **Migration schema drift**: When new migration columns are added (e.g., `job_queue_paused`, `default_max_retries`), test fixtures that mock the schema must be updated. Test `tests/integration/test_migration.py` was fixed (commit `8a41ca7`).

3. **Boolean config handling**: WebFetch server uses positive flag semantics — `True` emits `--flag`, `False` omits it entirely. Never generates `--no-flag` patterns. This is verified by `test_build_config_ignore_robots_txt_false`.

4. **Built-in server 403 protection**: DELETE and PUT on built-in servers return 403. Three separate tests cover this: delete, update name/description, update config.

5. **Frontend uses configureBuiltin for saves**: Not standard PUT — the dialog has triple mode (create/edit/configure-builtin) and uses the correct endpoint for each.

6. **Daemon integration test port**: Must use port > 10000 (used 18088) to avoid conflicting with the system's port 8088. NEVER kill processes on port 8088.

7. **Pre-existing failures**: 15 backend test failures are all in integration/e2e tests requiring LLM or specific environment setup. None are related to the built-in MCP servers feature.
