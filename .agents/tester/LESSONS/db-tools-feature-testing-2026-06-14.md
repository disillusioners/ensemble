# DB Tools Feature Testing — Findings & Notes

Date: 2026-06-14
Branch: `feature/db-tools`

## Summary
- 138/138 tests PASS across 4 test files
- All security boundaries enforced (SELECT guard, credential non-leak, duplicate name, encryption fallback)
- Integration wiring correct (category registration, tool filtering, meta.json allow lists)
- Mock interfaces fully match real source code
- ensure.md: dev.sh ran 30s without crash
- 0 quick fixes needed

## Test Architecture Insights

### Test Pyramid
- **Repository layer** (33 tests): Model + CRUD + credential isolation. Uses real `DbConnectionRepository` against in-memory SQLite — NOT mocks.
- **Pool manager** (61 tests): Largest test set. Sanitization, LIMIT detection, pool lifecycle. Uses MagicMock with `spec=` matching real `DbConnectionConfig` fields.
- **SELECT guard** (27 tests): Pure-function attack surface. Tests 9 forbidden keywords + 4 false-positive prevention patterns.
- **Tool integration** (17 tests): End-to-end. Uses real `CredentialManager` with real Fernet key for encryption verification.

### Key Security Architecture Confirmed
1. **Encryption at tool layer, not repository** — `DbConnectionRepository` takes engine only, receives pre-encrypted password
2. **SELECT guard is defense-in-depth** — True security boundary is DB-level read-only roles
3. **CredentialManager fallback closed** — Tool layer now rejects (not silently falls back) when Fernet unavailable
4. **IntegrityError sanitized** — `{type(exc).__name__}` used instead of `str(exc)` to prevent parameter leak
5. **LIMIT injection prevention** — `_has_limit_clause` detects real LIMIT while ignoring LIMIT in string literals, comments, column names

### Integration Wiring (Phase 4)
- `"db"` registered in `CATEGORY_MODULES` → `"daemon.tools.db_tools"`
- `create_instance_tools()` always creates 5 db tools, then `_apply_tool_filter()` expands/excludes based on agent allow list
- coder, devops, tester have `"db"` in `tools.allow`; all 13 others do not
- Category expansion: allow=["db"] → expands to all 5 tool names via `list_tools_by_category()`

## Minor Gaps (Non-blocking)
- No explicit test for special characters in connection *name* (covered for passwords)
- No explicit test for very long connection names (low risk, parameter-bound)
- No standalone semicolon-in-string-literal test for SELECT guard (keyword-in-literal tests provide stronger coverage)

These are low-risk because all SQL uses parameter-bound queries at the DB layer.
