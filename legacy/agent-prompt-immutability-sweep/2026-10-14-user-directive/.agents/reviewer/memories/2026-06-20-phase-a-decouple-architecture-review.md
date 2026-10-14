# Phase A Decouple-Architecture Review (2026-06-20)

## Verdict: REQUEST CHANGES
Branch: `feature/decouple-architecture`, commits a8c8a1fb..66ad9658 (8 commits, +5709/-649)

## 🔴 Critical Findings (2)

### C1 — A7 Race Window NOT Fully Closed (cross-thread TOCTOU)
- **File:** `daemon/services/job_feedback_observer.py:1203-1212` (C1 re-check), `:1314` (A7 gate), `:1561` (commit)
- **Issue:** C1 re-check at L1203 and A7 gate at L1314 are point-in-time reads. Between L1314 and `session.commit()` at L1561, dozens of GIL-release points exist (SQLAlchemy iteration, lock deletion loop). Event-loop thread runs `register_message_send` during this window.
- **Root cause:** `_finalize_job_db_sync` runs in a worker thread (via `asyncio.to_thread`). `is_complete()`/`get_pending_count()` read `_pending` without the per-parent asyncio.Lock. The GIL guarantees no torn reads but NOT sequence atomicity with the subsequent UPDATE.
- **Race trace:** resolve pops last entry → fires callback → worker thread does C1 re-check (passes) → GIL released during SQLAlchemy work → register_message_send lands on event loop → worker commits terminal UPDATE → late-registered child orphaned (its resolve finds no PROCESSING job, silently no-ops).
- **Fix:** Hold per-parent `asyncio.Lock` across the `asyncio.to_thread(_finalize_job_db_sync)` call in `_finalize_job`. Lock is event-loop-bound, never acquired on worker thread → no RuntimeError.

### C2 — Test Coverage Does Not Exercise Production Race
- **File:** `tests/test_correlation_authority_shadow.py:206-242`
- **Issue:** The "A12 register-window proof" test is sequential (`await late_task` after `gate.set()`), NOT real cross-thread interleaving. It never calls `resolve_response` to trigger the callback path. Other tests use mocked CM or mock `_finalize_job_db_sync`.
- **Fix:** Add real-threading test: real CM, real `register_message_send` during `_finalize_job_db_sync` SQLAlchemy work, assert no orphan.

## 🟡 Warnings (2)

### W1 — A0a rebuild_from_db Re-entrancy Unenforced
- **File:** `daemon/services/correlation_manager.py:662-870`
- **Issue:** Docstring says "NOT re-entrant" (L748-752) but NO runtime guard (`_rebuilding` flag / lock). Top-level `self._pending = {}` at L781 is outside any lock. A future admin-resync endpoint calling it twice would corrupt state.
- **Merge semantics:** OVERWRITE-at-top (correct) + MERGE-per-parent (correct, verified) confirmed working.

### W2 — A8 RuntimeError Swallowed by W3 Fail-Safe
- **File:** `daemon/services/job_feedback_observer.py:790-819`
- **Issue:** A8 RuntimeError at 7 sites propagates through `asyncio.to_thread` and is caught by broad `except Exception` (W3 fail-safe), converting "CM not initialized" into per-job FAILED transitions. Operator sees jobs failing one-by-one, not a single loud alert. The inner `except Exception: pass` (L817) means the FAILED transition itself can silently fail → stuck PROCESSING job.
- **Note:** This is a documented/tested design choice (test at regression.py:1110-1158 asserts it), but contradicts "hard error" framing in A8 commit messages.

## 🟢 Verified OK
- **Gating completeness:** All 6 control-flow reads of `waiting_for` correctly inside `elif use_legacy_cascade:` branches. NO leaks under flag OFF.
- **Flag default:** `use_legacy_waiting_for_cascade` defaults `False` in config.py:337, config.yaml:118 matches. `_config is None` fallbacks all default to False (safe).
- **A8 sites:** All 7 confirmed present, all raise RuntimeError BEFORE any terminal transition, no `except RuntimeError` swallows.
- **Kill switch:** Legacy path reachable and tested (990-line test pack).
- **`_check_invariant`:** Truly non-blocking (asyncio.to_thread, exceptions swallowed at DEBUG).
- **Callback/lock correctness:** completion_callback fires OUTSIDE per-parent lock (W1 fix ✅), H7 exception restore preserves _pending ✅.

## 🟢 Suggestions
- MySQL/MariaDB FOR UPDATE legacy path untested (only SQLite+PostgreSQL tested).
- `rearm_parent` docstring should clarify W1-fix lock-release prerequisite.

## Required for Merge
1. Fix A7 race (hold per-parent asyncio.Lock across to_thread call)
2. Add real-threading test exercising the race
3. Enforce rebuild_from_db re-entrancy with _rebuilding flag
4. Decide A8/W3 semantics (propagate RuntimeError OR update docs to say per-job FAILED)
