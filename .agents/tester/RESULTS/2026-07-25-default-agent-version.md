# Test Report: Default Agent Version Feature

**Date:** 2026-07-25
**Branch:** `feature/default-agent-version`
**Commits:** `062d98e2` (feature) + `7a4649cc` (fix: concurrency lock, version validation, optimistic rollback)
**Change size:** 960 lines across 21 files

---

## Summary

| Area | Status | Tests |
|------|--------|-------|
| **Backend API tests** (test_editor_settings.py) | ✅ PASS | 32/32 |
| **Backend ad-hoc verification** (7 requirements) | ✅ PASS | 8/8 checks |
| **Backend regression** (settings + registry + API) | ✅ PASS | 128 passed, 1 pre-existing fail |
| **Frontend tests** | ✅ PASS | 1798/1798 |
| **Frontend build** | ✅ PASS | No errors |
| **Overall** | ✅ **READY** | 0 feature-related failures |

---

## Backend Requirements (all 7 verified)

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 1 | `GET /api/settings/default-agent-versions` returns empty `{}` | ✅ PASS | `200 {'default_versions': {}}` |
| 2 | `PUT` sets default, persists, GET returns it | ✅ PASS | PUT 200 → GET `{'developer': 'v2'}` |
| 3 | `PUT` with invalid `version_tag` (e.g., "v99") → 422 | ✅ PASS | `422` returned |
| 4 | `PUT` with `version_tag: null` → resets to base | ✅ PASS | `200 {'default_versions': {}}` |
| 5 | `PUT` with empty/missing `agent_id` → 422 | ✅ PASS | Empty → 422; Missing → 422 |
| 6 | Concurrency: two rapid PUTs for different agents → both persist | ✅ PASS | `200/200` → `{'developer': 'v2', 'tester': 'v3'}` |
| 7 | No regressions in existing backend tests | ✅ PASS | 128 passed, 1 pre-existing failure (unrelated) |

### Pre-Existing Failure (NOT caused by this feature)
- `tests/test_api.py::test_send_message_success` — expects `enqueue_message_job(...)` without `queue_id` kwarg
- **Root cause:** The queue-selection feature (merged separately) added `queue_id=None` to the message-send path but this test assertion wasn't updated
- **Attribution:** Pre-existing on `latest`, unrelated to default-agent-version changes (settings.py, schemas.py, constants.py)

---

## Frontend Requirements (all 3 verified)

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 1 | Home page: version picker change persists (calls PUT API) | ✅ PASS | `setDefaultAgentVersion()` called on change; 3 spec tests cover persistence + optimistic rollback |
| 2 | Chat page: AgentSwitcher has NO version picker (removed) | ✅ PASS | HTML has no version `<select>`; `versionTag` is emitted passively from defaults map, not user-selected |
| 3 | Chat page: instance creation uses default version_tag | ✅ PASS | Full chain: API defaults → agent-switcher emit → chat component → `createInstance(agent, project, versionTag)` |

---

## Coverage Assessment

### Well Covered ✅
- API endpoint contracts (GET/PUT, validation, error codes)
- Concurrency safety (asyncio.Lock prevents data loss)
- Version validation (invalid tags rejected, null accepted)
- Home page persistence with optimistic update + rollback
- AgentSwitcher version picker removal

### Minor Coverage Gap (Low Priority)
- `chat.component.spec.ts` does not have a dedicated test asserting `versionTag` is forwarded to `createInstance`. The wiring is verified by code inspection + agent-switcher emission spec, but a dedicated integration test would strengthen confidence. Non-blocking — the data flow is correct.

---

## Overall Status: ✅ READY

The default agent version feature is fully functional and backward-compatible. All backend and frontend requirements pass. The single test failure is pre-existing and unrelated to this feature.
