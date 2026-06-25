# Phase 1: Constants & Utilities Foundation — Implementation Experience

## Date: 2026-04-23

## What Was Done
- Created `daemon/constants.py` with ~35+ named constants (API limits, SSE, timeouts, worker pool, error lengths, etc.)
- Appended to `daemon/utils.py` (existing 204 lines preserved): `parse_utc_datetime`, HTTP exception helpers, `create_service_dependency` factory, `validate_agent_id` (relocated from api.py)
- Updated `daemon/api.py`: removed validate_agent_id, added re-export, replaced magic numbers, replaced ~23 datetime.fromisoformat patterns
- Updated routers (jobs.py, dlq.py), services (worker_pool.py, task_processor.py), manager.py
- Updated test file imports

## Key Learnings
1. **Use `from daemon.models import ...`** NOT `from daemon.models.common import ...` — the `.common` submodule doesn't exist yet (Phase 2)
2. **validate_agent_id re-export**: Used `from .utils import validate_agent_id as validate_agent_id` in api.py for backward compat
3. **Test patches need updating**: When relocating a function, all `patch("daemon.api.get_registry")` in tests must change to `patch("daemon.utils.get_registry")`
4. **APPEND to utils.py**: Careful not to overwrite — the file has 5 existing functions (parse_think_tags, _extract_timestamp, serialize_message, get_next_sequence, compute_message_id)
5. **Magic number catalog**: Found many more magic numbers than initially expected — worker_pool.py already had some named constants (DEFAULT_TASK_TIMEOUT, MAX_ERROR_LEN) that were consolidated into constants.py

## Commit
`f946f07` — 22 files changed, 1806 insertions, 83 deletions
