# Quick Fix: Missing `from __future__ import annotations` for Slack Source

**Date**: 2026-05-31
**Branch**: feature/slack-source
**Commits**: c0c9847, 2854faf

## Problem
Python 3.10+ evaluates type annotations at runtime unless deferred with `from __future__ import annotations`. The Slack adapter code and related service files used `X | None` syntax and forward references (e.g., `"InstanceManager" | None`) which caused:
- `TypeError: unsupported operand type(s) for |: 'str' and 'NoneType'`
- `NameError` for forward-referenced types only imported under `TYPE_CHECKING`
- `SyntaxError` for nested f-string dict literals

## Root Cause
Files created during feature development didn't include `from __future__ import annotations` at the top. This works fine with `TYPE_CHECKING` imports but fails when annotations are evaluated at runtime (e.g., in `__init__` parameters, Pydantic models, etc.).

## Fix
Added `from __future__ import annotations` to ~17 files across:
- `daemon/sources/adapters/slack/` (all adapter files)
- `daemon/services/job_queue_service.py` (also fixed nested f-string)
- `daemon/tools/inner_soul.py`
- `daemon/services/job_processor.py`
- `daemon/services/job_feedback_observer.py`
- Various test files

## Lesson
**Always add `from __future__ import annotations`** to every new Python file in this project. The project uses Python 3.10+ features but doesn't enforce this globally, so each file needs it individually.
