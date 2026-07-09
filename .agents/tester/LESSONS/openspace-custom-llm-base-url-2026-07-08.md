# OpenSpace Custom LLM Base URL — Test Findings

**Date:** 2026-07-08
**Commit:** `a66982c7` on `feature/openspace-custom-llm`

## What Was Tested
OpenSpace Custom LLM Base URL feature adding `OPENSPACE_LLM_API_BASE` and `OPENSPACE_LLM_EXTRA_HEADERS` environment variable support.

## Key Findings

### Feature is well-implemented and fully tested
- 178 targeted tests all pass (98 + 80)
- 224 broader MCP tests pass (0 regressions)
- All 5 verification areas (env injection, userinfo validation, redaction, backward compat, full suite) pass

### Implementation details verified
- `_INJECTABLE_VARS` tuple in `openspace.py` (line 39-44) controls injectable env vars
- `build_config()` override (line 175-293) handles injection + userinfo validation
- `redact_secrets()` marker list extended to include `BASE` and `HEADERS`
- Changes are fully isolated to OpenSpace — webfetch/context7 server definitions untouched (git diff empty)

### Pre-existing webfetch failures (not from this commit)
Two webfetch bootstrap integration tests fail on BOTH this commit and the parent commit `e8b03550`:
1. `TestWebFetchBootstrapIntegration::test_bootstrap_creates_webfetch_server` — D13 migration unconsumed columns issue
2. `TestWebFetchBootstrapIntegration::test_schema_drift_removes_stale_flag` — mcp-server-fetch CLI version drift

These are environmental issues, NOT regressions from the OpenSpace LLM changes.
