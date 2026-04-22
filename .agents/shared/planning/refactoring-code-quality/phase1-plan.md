# Phase 1: Constants & Shared Utilities

## Objective
Extract all magic numbers into named constants, relocate `validate_agent_id` from `api.py` to the **existing** `daemon/utils.py`, and append shared utility functions (datetime parsing, HTTPException helpers, service dependency injection). This phase creates the foundation that all subsequent phases build on.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: —
- **Shared files with other phases**: `daemon/constants.py` (new), `daemon/utils.py` (modified) — consumed by all later phases
- **Shared APIs/interfaces**: Constants module + utils functions
- **Why this coupling**: Foundation layer — everything else depends on these constants and utilities

## Pre-flight Validation
```bash
# 1. Record baseline test results
python -m pytest tests/ -v --tb=short 2>&1 | tee /tmp/refactor-baseline.txt

# 2. Create rollback point
git tag refactor-pre-phase1

# 3. Verify current imports
grep -r "from daemon.api import validate_agent_id" daemon/ tests/ --include="*.py"
grep -r "from daemon.api import send_message" tests/ --include="*.py"
```

## Rollback Procedure
```bash
git checkout refactor-pre-phase1 -- daemon/constants.py daemon/utils.py daemon/api.py daemon/manager.py
# Re-run tests to verify clean state
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `daemon/constants.py` | **New file** with all magic numbers organized by category | `daemon/constants.py` (new) |
| 2 | Extract API limits | `DEFAULT_PAGE_LIMIT=100`, `MAX_PAGE_LIMIT=100`, `MIN_PAGE_LIMIT=1`, `MAX_CONCURRENT_INSTANCES=20`, `RECENT_WINDOW_SIZE=10`, `DLQ_MAX_ENTRIES=50` | `daemon/constants.py` |
| 3 | Extract timeouts | `REQUEST_TIMEOUT_S=660`, `INSTANCE_TIMEOUT_S=60`, `SSE_TIMEOUT_S=30`, `SHUTDOWN_TIMEOUT_S=300` | `daemon/constants.py` |
| 4 | Extract retry/queue constants | `DEFAULT_RETRY_COUNT=3`, `MAX_QUEUE_DEPTH=5`, `LLM_TRANSIENT_RETRIES=10`, `BACKOFF_BASE_S=60`, `BACKOFF_MAX_S=3600`, `BACKOFF_MULTIPLIER=2.0` | `daemon/constants.py` |
| 5 | Extract rate limit constants | `TELEGRAM_RATE_LIMIT=(30, 30)`, `WEBHOOK_RATE_LIMIT=(100, 100)`, `WHATSAPP_RATE_LIMIT=(10, 20)` | `daemon/constants.py` |
| 6 | Extract DB constants | `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=10`, `DB_BUSY_TIMEOUT_S=30` | `daemon/constants.py` |
| 7 | **APPEND** datetime helper to `daemon/utils.py` | **APPEND** (do NOT replace existing content) — add `parse_utc_datetime(value: str \| datetime) -> datetime` after the existing functions (after line 204). This replaces 32 inline occurrences. | `daemon/utils.py` (existing, 204 lines → ~220 lines) |
| 8 | **APPEND** HTTPException helpers to `daemon/utils.py` | **APPEND** `raise_not_found(detail)`, `raise_service_unavailable(detail)`, `raise_bad_request(detail)` — replacing verbose patterns in routers | `daemon/utils.py` |
| 9 | **APPEND** service dependency factory to `daemon/utils.py` | **APPEND** `create_service_dependency(service_type)` factory — replacing boilerplate in routers | `daemon/utils.py` |
| 10 | **Relocate** `validate_agent_id` from `daemon/api.py` to `daemon/utils.py` | **Move** the function (currently at api.py lines 100–127) to `daemon/utils.py`. Keep a re-export in `api.py`: `from daemon.utils import validate_agent_id as validate_agent_id`. Update `routers/jobs.py:166` to import from `daemon.utils` directly. | `daemon/utils.py`, `daemon/api.py`, `daemon/routers/jobs.py` |
| 11 | Update `api.py` magic numbers | Replace inline magic numbers with constant imports in `api.py` | `daemon/api.py` |
| 12 | Update `manager.py` magic numbers | Replace inline magic numbers with constant imports in `manager.py` | `daemon/manager.py` |
| 13 | Update `job_queue_service.py` magic numbers | Replace inline magic numbers with constant imports | `daemon/services/job_queue_service.py` |
| 14 | Update other service files | Replace magic numbers in `worker_pool.py`, `task_processor.py` | `daemon/services/worker_pool.py`, `daemon/services/task_processor.py` |

## Key Files

### Existing Files Modified (NOT replaced)
- **`daemon/utils.py`** (existing, 204 lines) — **APPEND** new functions after line 204. Existing functions to preserve:
  - `parse_think_tags` (lines 12–32)
  - `_extract_timestamp` (lines 36–55)
  - `serialize_message` (lines 58–164)
  - `get_next_sequence` (lines 171–183)
  - `compute_message_id` (lines 187–204)
- **`daemon/api.py`** — Replace magic numbers, relocate `validate_agent_id` (add re-export)
- **`daemon/manager.py`** — Replace magic numbers only (no structural changes)
- **`daemon/services/job_queue_service.py`** — Replace magic numbers only
- **`daemon/services/worker_pool.py`** — Replace magic numbers only
- **`daemon/services/task_processor.py`** — Replace magic numbers only
- **`daemon/routers/jobs.py`** — Update import at line 166: `from daemon.api import validate_agent_id` → `from daemon.utils import validate_agent_id`

### New Files Created
- **`daemon/constants.py`** — All named constants organized by category

## Constraints
- **`daemon/utils.py` MUST be APPENDED TO, not replaced** — all 5 existing functions must be preserved exactly
- Constants must have **exact same values** as current inline numbers
- `parse_utc_datetime()` must produce **identical output** to the current inline pattern
- HTTPException helpers must produce **identical HTTP responses** (same status codes, same detail format)
- `validate_agent_id` must remain importable from `daemon.api` via re-export (backward compat for Phase 3)
- Do NOT delete old inline code — just add imports and replace in-place

## Detailed Implementation Notes

### `daemon/constants.py` Structure (NEW file)
```python
"""Named constants for the agents-ensemble daemon."""

# ── API Limits ──
DEFAULT_PAGE_LIMIT: int = 100
MAX_PAGE_LIMIT: int = 100
MAX_CONCURRENT_INSTANCES: int = 20
RECENT_WINDOW_SIZE: int = 10
DLQ_MAX_ENTRIES: int = 50

# ── Timeouts (seconds) ──
REQUEST_TIMEOUT_S: int = 660
INSTANCE_TIMEOUT_S: int = 60
SSE_TIMEOUT_S: int = 30
SHUTDOWN_TIMEOUT_S: int = 300

# ── Retry & Queue ──
DEFAULT_RETRY_COUNT: int = 3
MAX_QUEUE_DEPTH: int = 5
LLM_TRANSIENT_RETRIES: int = 10

# ── Backoff ──
BACKOFF_BASE_S: int = 60
BACKOFF_MAX_S: int = 3600
BACKOFF_MULTIPLIER: float = 2.0

# ── Rate Limits (requests, period_seconds) ──
TELEGRAM_RATE_LIMIT: tuple[int, int] = (30, 30)
WEBHOOK_RATE_LIMIT: tuple[int, int] = (100, 100)
WHATSAPP_RATE_LIMIT: tuple[int, int] = (10, 20)

# ── Database ──
DB_POOL_SIZE: int = 5
DB_MAX_OVERFLOW: int = 10
DB_BUSY_TIMEOUT_S: int = 30
```

### Appending to `daemon/utils.py` (after line 204)
```python
# ── DateTime Utilities ──

from datetime import datetime, timezone

def parse_utc_datetime(value: str | datetime) -> datetime:
    """Parse a datetime string or pass through a datetime object, ensuring UTC."""
    if isinstance(value, str):
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return value

# ── HTTP Exception Helpers ──

from fastapi import HTTPException

def raise_not_found(detail: str = "Resource not found") -> None:
    raise HTTPException(status_code=404, detail=detail)

def raise_service_unavailable(detail: str = "Service not initialized") -> None:
    raise HTTPException(status_code=503, detail=detail)

def raise_bad_request(detail: str = "Bad request") -> None:
    raise HTTPException(status_code=400, detail=detail)

# ── Service Dependency Factory ──

from typing import TypeVar, Optional

T = TypeVar("T")

def create_service_dependency(service_type: type[T]):
    """Creates get/set functions for FastAPI service injection."""
    _instance: Optional[T] = None

    def get_service() -> T:
        if _instance is None:
            raise_service_unavailable(f"{service_type.__name__} not initialized")
        return _instance

    def set_service(instance: T) -> None:
        nonlocal _instance
        _instance = instance

    get_service.set_service = set_service
    return get_service

# ── Agent Validation (relocated from daemon.api) ──

from pathlib import Path
from daemon.loader import get_registry
from daemon.models.common import ErrorCodes, ErrorResponse

def validate_agent_id(agent_id: str) -> tuple[str, Path]:
    """Validate agent_id exists and return agent_id with path."""
    registry = get_registry()
    metadata = registry.get(agent_id)
    if metadata is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Agent not found: {agent_id}"
            ).model_dump()
        )
    return agent_id, metadata.path
```

### `validate_agent_id` Relocation Details

**Current location**: `daemon/api.py` lines 100–127

**Consumers that import from `daemon.api`**:
| File | Line | Import |
|------|------|--------|
| `daemon/routers/jobs.py` | 166 | `from daemon.api import validate_agent_id` (inline import) |
| `tests/test_spawn_instance_instructive_errors.py` | 14 | `from daemon.api import validate_agent_id` |
| `tests/unit/test_vision.py` | 705, 742 | `from daemon.api import send_message` (note: `send_message` is an endpoint, NOT this function — but tests import it) |

**Steps**:
1. Move function body to `daemon/utils.py`
2. In `daemon/api.py`, add: `from daemon.utils import validate_agent_id as validate_agent_id` (backward compat)
3. In `daemon/routers/jobs.py` line 166, change: `from daemon.api import validate_agent_id` → `from daemon.utils import validate_agent_id`
4. In `tests/test_spawn_instance_instructive_errors.py` line 14, change: `from daemon.api import validate_agent_id` → `from daemon.utils import validate_agent_id`

> **Important**: The `send_message` function in `daemon/api.py` (lines 852–907) is a FastAPI endpoint handler. It is imported by `tests/unit/test_vision.py` for testing. This endpoint will be moved to `daemon/routers/messages.py` in Phase 3. For now, do NOT move it — only relocate `validate_agent_id`.

## Deliverables
- [ ] `daemon/constants.py` created with all magic numbers
- [ ] `daemon/utils.py` APPENDED with `parse_utc_datetime`, HTTPException helpers, service dependency factory, `validate_agent_id`
- [ ] All 5 existing utils functions preserved unchanged
- [ ] All inline magic numbers replaced with constant imports
- [ ] All 32 datetime parsing occurrences replaced with `parse_utc_datetime()`
- [ ] `validate_agent_id` relocated to `daemon/utils.py` with re-export from `daemon/api.py`
- [ ] `routers/jobs.py` updated to import `validate_agent_id` from `daemon.utils`
- [ ] Full test suite passes (identical to baseline)
