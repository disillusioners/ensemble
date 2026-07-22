# Live Endpoint Testing — Stale Binary Trap

**Date:** 2026-07-22
**Test:** Language Preference Endpoint Fix (fix/stale-system-project-id-import)

## Context
When verifying the language preference endpoint fix live, the worker found port 9797 was serving `./ensemble-prod` (compiled binary) that predates the fix commit. It still exhibited the original bug (503 on PUT), while `./dev.sh` on port 8079 (running from fixed Python source) worked correctly.

## Lesson
**When testing live endpoints, the port may serve a stale binary, not the fixed source.**

- Port 9797 = `./ensemble-prod` (compiled binary) — predates fix → reproduces old bug
- Port 8079 = `./dev.sh` (Python source) — reflects current source → works correctly

**Action for future live tests:**
1. Probe multiple ports (8079 and 9797) to detect which is running.
2. Prefer `./dev.sh` (port 8079) for source-level verification — it always runs current code.
3. If a production binary is stale, note it as a deployment action item — do NOT kill it unless you started it.
4. Document which port each test used in the report.

## Rule Reinforced
Never kill processes you didn't start. Never kill port 8088 (ensemble self-system).
