# Quick Fix: config.yaml + manager.py + api.py session→instance rename

**Date:** 2025-04-05
**Commit:** 44f0025
**Session:** ses_2a48a6a42ffeE8IECvmCfArbFM

## Issue
Daemon failed to start with Pydantic validation errors — `config.yaml` had old `session` naming keys under `limits:` but the Pydantic model used `instance` naming.

## Root Cause
Merge of `feature/context-compaction` brought in an older `config.yaml` with session naming, while the code had been refactored to use instance naming.

## Fixes Applied

### 1. config.yaml (primary fix)
- `max_sessions` → `max_instances`
- `max_children_per_session` → `max_children_per_instance`
- `session_timeout_minutes` → `instance_timeout_minutes`

### 2. daemon/manager.py:421 (quick fix — discovered during verification)
- `session_repo=self._instance_repository` → `instance_repo=self._instance_repository`

### 3. daemon/api.py:187 (quick fix — discovered during verification)
- `session_manager=manager` → `instance_manager=manager`

## Verification
- Daemon starts: "Application startup complete"
- `/docs` returns 200 OK
- `/` returns JSON response
- ensure.md requirement met: dev.sh runs successfully
