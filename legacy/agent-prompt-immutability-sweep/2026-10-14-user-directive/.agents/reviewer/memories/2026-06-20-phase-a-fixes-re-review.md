# Phase A Fix Re-Review (commit ef147bfa) — 2026-06-20

## Verdict: REQUEST CHANGES
The C1 fix is deadlock-free and does narrow the window, BUT:
- Test 2 (C2) is a FALSE POSITIVE — proven empirically (passes with C1 fix disabled)
- The C1 fix DEFERS the orphan rather than eliminating it
- The W2 fix is INEFFECTIVE — RuntimeError is swallowed by both callers' wrappers

## C1 Fix Analysis
**Deadlock-free: YES** ✅ — `asyncio.to_thread` yields control to the loop; worker thread never acquires the per-parent lock; blocked register coroutine is scheduled but waits for the lock.

**Race closed: PARTIALLY** 🟡 — The fix prevents register from interleaving between re-check and commit. BUT this means if pre-check saw 0 pending, re-check also sees 0, commit proceeds, THEN register runs after lock release. The orphan is DEFERRED, not eliminated. When child B later resolves → callback → `_get_processing_job_for_instance` returns None (job is COMPLETED) → silently skips → ORPHAN CONFIRMED.

The C1 re-check at L1251 is effectively DEAD CODE in the CM-active path — the register can never interleave with it because the lock holds it out.

**New contention:** Holding per-parent lock across DB I/O serializes all CM ops (register/resolve/clear/rearm) for that parent. Slow DB = per-parent CM stall.

## C2 Fix Analysis
**Test 1:** Valid prerequisite only. Proves the LOCK mechanism works but does NOT prove `_finalize_job` acquires the lock (it manually holds the lock in a background task, never calls `_finalize_job`). Coverage gap.

**Test 2:** 🔴 FALSE POSITIVE — proven empirically:
- WITH C1 fix: passes in 5.7s (register blocked by lock for 5s poll timeout; re-check sees pre-seeded child A pending=1, defers)
- WITHOUT C1 fix: passes in 0.6s (register completes immediately; re-check sees child A + B pending=2, defers)
- Test passes REGARDLESS of C1 fix because pre-seeded child A (pending=1) triggers the defer, NOT the concurrent child B.
- The docstring claim "re-check sees CM.pending > 0 (B's registration was serialized before re-check ran)" is logically impossible — B is blocked by the lock that holds it out.

## W1 Fix Analysis
✅ CORRECT. `_rebuilding` flag with try/finally. Check+set is atomic (no await between them) in single-threaded asyncio. Finally resets on exception. RuntimeError caught by `start()` but flag resets, so subsequent calls work. Defensive guard as intended.

## W2 Fix Analysis
🟡 INEFFECTIVE. The `except RuntimeError: raise` prevents the W3 fail-safe from swallowing it, BUT both production callers have broad `except Exception` wrappers that catch it anyway:
1. CM callback path: `resolve_response` L348 `except Exception:` (H7 restore) — swallows + restores _pending + logs
2. `_process_event` path: L258/313 `except Exception:` (event loop wrapper) — swallows + logs + continues

The RuntimeError is now LOUDER (logged with traceback via logger.exception) but does NOT crash the daemon or reach operators as a process-level alert. The W2 fix's stated goal ("propagate as true hard errors") is NOT achieved in either production path.
