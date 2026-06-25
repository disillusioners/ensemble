# Phase 2 Implementation — Input Normalization (project_id)

## Key Learnings

1. **Dual-binding issue with module-level constants**: When `normalize_project_id()` imports `SYSTEM_DEFAULT_PROJECT_ID` at module level (`from daemon.constants import SYSTEM_DEFAULT_PROJECT_ID`), it creates a local binding to `None`. Tests that later set `constants.SYSTEM_DEFAULT_PROJECT_ID = "test"` won't affect this binding. Fix: either use `import daemon.constants; constants.SYSTEM_DEFAULT_PROJECT_ID` (attribute access), or patch both `constants.SYSTEM_DEFAULT_PROJECT_ID` AND `project_normalizer.SYSTEM_DEFAULT_PROJECT_ID` in tests.

2. **Canonical chokepoint pattern**: Placing normalization at the TOP of `enqueue()` is the single most important change — it covers ALL callers (HTTP, tools, retry, internal). Boundary normalizations are defense-in-depth only. The enqueue chokepoint must be before ANY other logic (idempotency check, queue lookup, etc.).

3. **Pydantic field_validator with deferred imports**: Using `field_validator("project_id", mode="before")` with the `normalize_project_id` import inside the validator function body avoids circular imports at module level. This is the right pattern for cross-module validators.

4. **instance_lifecycle.py pattern**: The old code at lines 104-106 converted "null"/"none"/"" to Python `None`. Phase 2 replaces this with `normalize_project_id()` which converts to `SYSTEM_DEFAULT_PROJECT_ID` instead — a subtle but critical behavioral change that prevents orphans.

5. **Test infrastructure for module constants**: Integration tests needed a `conftest.py` fixture that patches `normalize_project_id` to dynamically read `SYSTEM_DEFAULT_PROJECT_ID` instead of using the import-time binding. This is necessary when testing code that captures module-level constants at import time.

## Architecture
- `daemon/services/project_normalizer.py` — standalone utility, no class, pure function
- Normalization layers: Schema → Router → Tool → **enqueue()** (canonical) → Service
- `instance_lifecycle.py` and `tools/instance.py` bypass the HTTP/tool layers, so they're covered by the enqueue chokepoint but also have their own normalization for clarity

## Files Changed (11 files, +914 lines)
- daemon/services/project_normalizer.py (new)
- daemon/services/job_queue_service.py (canonical chokepoint)
- daemon/routers/schemas.py (Pydantic validator)
- daemon/routers/jobs_crud.py (defense-in-depth)
- daemon/tools/job_queue.py (defense-in-depth)
- daemon/services/instance_lifecycle.py (R1 fix)
- daemon/tools/instance.py (R1 fix)
- tests/unit/test_project_normalizer.py (new)
- tests/unit/test_schemas.py (new)
- tests/job_queue/test_retry_orphan_normalization.py (new)
- tests/integration/test_job_create.py (new)
- tests/integration/conftest.py (modified)
