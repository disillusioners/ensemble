# 2026-08-16 — Auto-Restart Phase 1 Deep Review (feature/auto-restart-phase1)

**Verdict:** APPROVE-WITH-NOTES (unanimous council, 2 models). 0 critical / 4 major / 11 minor.
**Mode:** Deep-Review council (`convene_council_with_skill`, code-review skill, 2 councilors).
**Base:** 16e78344, 15 commits, +4319/−40, 22 files.

## Key findings (recurring-value lessons)

1. **uvicorn `timeout_graceful_shutdown` (0.41.0) bounds ONLY `_wait_tasks_to_complete()`** (in-flight requests), NOT the FastAPI lifespan teardown (`uvicorn/lifespan/on.py` awaits `shutdown_event.wait()` unbounded). A "bounded shutdown" flag on `uvicorn.run` does not make lifespan hooks bounded — per-step `asyncio.wait_for` budgets are needed in `manager.shutdown()`. M1, highest-confidence (converged 2/2, source-verified).
2. **Second SIGTERM during uvicorn shutdown → `force_exit=True` → lifespan shutdown SKIPPED.** Stop scripts must TERM the supervisor only when one exists; never both supervisor and child (M2, scripts/stop-ensemble.sh:196-208). Crash-equivalent stops are the classic hidden failure of wrapper scripts.
3. **Stop-script wait budgets must be derived from the daemon's graceful-shutdown budget + margin** (WAIT_S=10 vs 60s budget, M3). Wait defaults that predate a new shutdown budget silently discard it.
4. **Never `cp` a binary over a running process** — `O_TRUNC` on mapped Mach-O → SIGBUS (macOS); ETXTBSY (Linux). Stage to temp + atomic `mv` (M4, deploy.sh:271 before :287-289).
5. **tz-naive DB columns + aware-UTC binds → SQL-side freshness math is the right call** (`now() - MAX(col)` in SQL vs Python-side subtraction). Endorsed deviation (a); pinned by PG tz-regression test.
6. **Auth-failure exit code must be budget-exempt-AND-non-retryable-distinct (78/EX_CONFIG)** so the exempt track (75) can't spin forever on unrecoverable config. Endorsed deviation (c); defensive SQLSTATE allowlist (28P01/28000/28P02 only; 57P03 stays 75).

## Process notes

- This deployment's canonical councilor models are `agentic` + `coding` only; requesting Claude/GPT/Gemini trio → governor Step-0 STOP (clarifying question) → revive same governor with option A. Revival worked cleanly, context preserved.
- Dirty planning docs in worktree during a branch review → instruct councilors to `git show HEAD:<path>`; review only `git diff --stat base..HEAD` files.
- Mandatory-e2e trigger (job/task/queue files) does NOT fire for additive lifespan wiring + read-only probes; verified by grep of diff name-only.
- Todo graph was cleared across turn revival — fan-in state can vanish between async turns; verify with `todo_view` before/after.
