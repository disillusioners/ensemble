# Test Report: OpenSpace Custom LLM Base URL Feature

**Date:** 2026-07-08 21:09 UTC
**Branch:** `feature/openspace-custom-llm`
**Commit:** `a66982c7` — *feat: add OPENSPACE_LLM_API_BASE and OPENSPACE_LLM_EXTRA_HEADERS env injection*
**Sessions:** `openspace-llm-targeted`, `openspace-llm-regression`

---

## Summary

| Area | Status | Detail |
|------|--------|--------|
| 1. Full test suite (targeted) | ✅ PASS | 178/178 (98 + 80) |
| 2. Env injection | ✅ PASS | 5/5 sub-checks |
| 3. Userinfo validation | ✅ PASS | 3/3 sub-checks |
| 4. Redaction | ✅ PASS | 3/3 sub-checks |
| 5. Backward compatibility | ✅ PASS | webfetch/context7 unaffected |
| Regression (broader MCP) | ✅ PASS | 98 + 126 passed, 0 regressions |
| **Overall** | **✅ PASS** | **No fixes needed, no commit required** |

---

## Detailed Results

### Part 1: Full Test Suite — PASS

| File | Tests | Passed | Failed | Time |
|---|---|---|---|---|
| `tests/unit/mcp/test_openspace_builtin.py` | 98 | **98** | 0 | 0.76s |
| `tests/unit/test_mcp_server_crud.py` | 80 | **80** | 0 | 1.70s |
| **Total** | **178** | **178** | **0** | — |

### Part 2: Env Injection — PASS (5/5)

| # | Check | Result | Observed |
|---|---|---|---|
| 1 | `OPENSPACE_LLM_API_BASE` set → in `config["env"]` | ✅ PASS | `https://llm.internal/v1` |
| 2 | `OPENSPACE_LLM_API_BASE` not set → absent | ✅ PASS | key absent |
| 3 | `OPENSPACE_LLM_EXTRA_HEADERS` set → in `config["env"]` | ✅ PASS | `{"X-Auth":"Bearer tkn"}` |
| 4 | `OPENSPACE_LLM_EXTRA_HEADERS` not set → absent | ✅ PASS | key absent |
| 5 | `OPENSPACE_LLM_API_KEY` + `OPENSPACE_API_KEY` still injected | ✅ PASS | both present |

**Test coverage:** `TestOpenSpaceLLMConfigInjection` (10 tests, including strip/empty/whitespace edge cases)

### Part 3: Userinfo Validation — PASS (3/3)

| # | Input | Expected | Result |
|---|---|---|---|
| 1 | `http://user:pass@host:8080` | `McpConfigValidationError` | ✅ PASS — raises *"must not contain userinfo credentials"* |
| 2 | `http://localhost:8080/v1` | passes | ✅ PASS — value injected |
| 3 | `https://my-gateway.example.com/v1` | passes | ✅ PASS — value injected |

**Test coverage:** `TestOpenSpaceLLMApiBaseValidation` (5 tests)

### Part 4: Redaction — PASS (3/3)

| # | Check | Result |
|---|---|---|
| 1 | `OPENSPACE_LLM_API_BASE` value → `[REDACTED]` | ✅ PASS |
| 2 | `OPENSPACE_LLM_EXTRA_HEADERS` value → `[REDACTED]` | ✅ PASS |
| 3 | `PATH`, `OPENSPACE_MODEL` still visible | ✅ PASS |

**Test coverage:** `TestRedactSecretsUtility` (15 tests, including end-to-end POST through FastAPI router)

### Part 5: Backward Compatibility — PASS

- Existing `OPENSPACE_LLM_API_KEY` / `OPENSPACE_API_KEY` injection still works (7 tests)
- `OPENSPACE_MCP_TRANSPORT=stdio` pin still applied
- HTTP-mode warning contract for new vars covered (4 tests)
- webfetch/context7 server definitions **untouched** (git diff empty)
- 25/25 context7 tests pass; 35/37 webfetch tests pass (2 failures are **pre-existing**, confirmed by running on parent commit `e8b03550`)

---

## Regression Check — PASS

### Full MCP Unit Suite
- **98 passed, 0 failed** — `tests/unit/mcp/`

### Router/Service Regression
- **126 passed, 0 failed** — `test_mcp_server_crud.py` (81) + `test_mcp_service.py` (45)

### Pre-existing Failures (NOT caused by this commit)

| File | Test | Root Cause |
|---|---|---|
| `tests/unit/test_webfetch_builtin.py` | `TestWebFetchBootstrapIntegration::test_bootstrap_creates_webfetch_server` | D13 migration: unconsumed columns (cancelled_at, error_message) |
| `tests/unit/test_webfetch_builtin.py` | `TestWebFetchBootstrapIntegration::test_schema_drift_removes_stale_flag` | External CLI version drift (mcp-server-fetch) |

Both confirmed pre-existing on parent commit `e8b03550`. Not regressions.

---

## Quick Fixes Applied: None

No fixes were needed. All tests pass on first run. No commit required.

---

## Overall Status

- Unit Tests: ✅ PASS (178/178 targeted, 224/224 broader)
- Regression: ✅ PASS (0 regressions, 2 pre-existing failures unrelated)
- ensure.md: N/A (MCP feature-specific testing, no quality gates violated)
- **Testing Complete**: ✅ READY — Feature is verified and ready for merge
