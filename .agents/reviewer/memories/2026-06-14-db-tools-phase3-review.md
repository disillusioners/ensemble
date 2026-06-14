# Phase 3 — Database Tool Category Code Review

**Date:** 2026-06-14
**Commit:** 7defd2e (branch feature/db-tools)
**Review Mode:** 🔴 Deep-Review (Council — Data Integrity/Security + Business-Critical Logic triggers)
**Scope:** `daemon/tools/db_tools.py` (NEW, 553 lines), `_tool_registry.py` (MOD), `instance.py` (MOD)

## Verdict: 🟡 Needs Work (1 fix before merge)

0 CRITICAL · 2 WARNING · 6 INFO

The architecture is sound. The SELECT guard passes 30+ bypass vectors. The two real issues are an error-message credential leak (F2) and a silent encryption fallback (F1).

## Key Findings

| ID | Severity | Area | Summary |
|----|----------|------|---------|
| F2 | 🟡 WARNING | Security | `db_conn_add` IntegrityError leaks credentials via `[parameters:]` in error string |
| F1 | 🟡 WARNING | Security | `encrypt()` silently falls back to plaintext JSON when crypto not configured |
| F3 | 🟡 WARNING | Performance | LIMIT injection skipped when "LIMIT" appears in string literals or identifiers |
| F4 | 🟢 INFO | Correctness | Dollar-quoted strings ($$) cause false positives (rejects valid queries) |
| F5 | 🟢 INFO | Correctness | Nested block comments cause false positives |
| F6 | 🟢 INFO | Safety | No validation/clamping on max_rows and timeout parameters |
| F7 | 🟢 INFO | Correctness | Line 515 "Rows: N (of N)" is redundant/uninformative |
| F8 | 🟢 INFO | Consistency | DB tools added unconditionally — no feature flag or category gate |

## SELECT Guard Assessment

**PASS.** The `_validate_select_only()` function was tested against 30+ vectors including:
- Multi-statement injection ✓ (blocked)
- SELECT INTO ✓ (blocked via INTO keyword)
- Comment injection (--, /* */) ✓ (stripped)
- Keyword-in-string false positives ✓ (strings stripped before scan)
- WITH ... INSERT (CTE DML) ✓ (blocked by INSERT keyword)
- Parenthesized subqueries ✓ (allowed, correct)
- DO blocks, COPY, PREPARE, EXECUTE, VALUES, TABLE ✓ (blocked by first-keyword check)
- UNION SELECT ✓ (allowed, correct — no forbidden keyword)

Dollar-quoted strings (F4) and nested comments (F5) cause **false positives** (rejecting valid queries), NOT bypasses. The guard is conservatively safe.
