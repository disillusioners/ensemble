# Live Reproduction: Pause/Resume/Terminate Tree-Propagation Bugs (dev environment)

**Date:** 2026-08-24 (17:00–18:20 UTC)
**Task:** Empirical reproduction of user report "pause/resume and terminated feature — tree not broadcast" — repro + evidence ONLY, no fixes, no code changes.
**Method:** 6 sequential phases via 6 worker dispatches (0 direct executions); live dev daemon `./dev.sh` @ port 8079 (pid 12539, boot 17:00:53Z, v0.11.0, postgres). All raw evidence in `/tmp/pause-repro-20260824/` (state.json + ~130 evidence files + full daemon log `dev-daemon.log`, baseline 248 lines → ~3,500).
**Tree topology used throughout:** project `09b6c42d-5865-404f-b7fa-04dbe816bb11`; root leader `f5e223f1` → children tester `c83b46cd` + developer `73950d87` → grandchild workers under tester (5 across rounds: `41b33442`, `c9b1399b`, `763960eb`, `994369e9`, +1 r4). Delegation rounds kept the tree busy with long sleeps (420–480s).

## VERDICT: USER REPORT CONFIRMED — 4 critical propagation defects + 3 secondary bugs

| # | Defect | Severity | Trigger |
|---|--------|----------|---------|
| B1 | **Pause does not cascade DOWN** — root pause leaves descendants running; they start NEW work under a paused root | 🔴 critical | `POST /api/instances/{root}/pause` while descendants busy |
| B2 | **Resume strands the root when children completed DURING the pause** — buffered child reports never delivered; root `running` forever; only a NEW external message rescues | 🔴 critical | pause → children complete while paused → `POST .../resume` |
| B3 | **Terminate has no UP propagation** — parent's dependency watcher is CANCELLED (not fired-with-outcome); parent defers completion forever on a ghost child | 🔴 critical | `DELETE /api/instances/{mid_tree_child}` while parent waits on it |
| B4 | **Terminate-root DOWN propagation misses live children** — cascade enumerates transient hierarchy (`children=0`), missing permanently-parented live instances → orphan + perpetual GUARD livelock | 🔴 critical | `DELETE /api/instances/{root}` while a pre-churn child still running |
| B5 | **`/stop` acts on the WRONG instance** — ignores path parameter, pauses project ROOT instead | 🟠 important | `POST /api/instances/{X}/stop` for any X ≠ root |
| B6 | **Instance detail endpoint 404s after resume cascade** — in-memory state wiped, not rehydrated | 🟠 important | after `resume` (observed phase 4: all 5 instances 404) |
| B7 | **Work/job row integrity anomalies** — future-dated rows (+7h), `completed_at` re-stamped on resume, jobs-detail vs jobs-list status disagreement | 🟢 nice-to-have | 3 independent observations (phases 3, 4, 6a) |

---

## B1 — Pause: no DOWN propagation (Phase 3)

- **Request:** `POST /api/instances/f5e223f1-.../pause` @ 17:15:45Z (busy tree: developer + grandchild mid-`sleep 420`, tester `waiting_children`).
- **Response:** `200 {"paused":true,"paused_ids":["f5e223f1-…"],"skipped_ids":[]}` — only the root id.
- **EXPECTED:** entire subtree paused. **ACTUAL (3 sweeps t+2s/15s/60s, `phase3-postpause-t*.json`):**

| Instance | t+2s | t+15s | t+60s |
|---|---|---|---|
| root leader | paused | paused | paused |
| tester (child) | waiting_children | waiting_children | waiting_children |
| developer (child) | running | running | running |
| worker r2 (grandchild) | running | running | running — **started `sleep 420` at 17:16:03, 18s AFTER root pause** (log 1450) |

- **Smoking gun:** log line 1450 `bash sleep 420 && echo "sleep completed"` — new work began under a paused root. Only ONE "Pausing instance" line (1441) exists in the entire 301-line delta; zero cascade-pause lines.
- **Drift reconciler blind:** `reconcile_drift_states … reconciled=0` @ 17:16:31 (line 1462) — paused-root-with-running-descendants not flagged.

## B2 — Resume: misleading success + stranded root (Phases 4 & 6a)

- **Request:** `POST /api/instances/f5e223f1-.../resume` (body `{}`) — executed twice: 17:20:57Z (phase 4) and 17:45:36Z (phase 6a).
- **Response (both):** `200 {"resumed":true,"resumed_ids":["f5e223f1-…"],"resume_results":{"f5e223f1-…":{"status":"no_active_job"}}}` — **HTTP 200 + `resumed:true` while the resume actually did nothing.**
- **Mechanism (log 1535–1540, 2655–2681):** `route_outcome=invalid_or_missing_handle — no suspended or paused turn found` → no dispatch; `resume_cascade_db_sync: resumed 0 task(s) PAUSED → PENDING [work_ids=[]]` → nothing requeued; `_compact_fired_watchers_for_paused: deleted 3–4 FIRED watcher(s)` → **wake signals destroyed**; job_processor then polls forever ("1 queue(s) with admittable work") claiming nothing.
- **Two outcomes, one root cause:**
  - **Phase 4 (recovered by luck):** children were still running at resume → completed AFTER → live dependency-bus watchers fired (`emit_terminal … target=f5e223f1` @ 17:22:29, 17:23:44) → root re-registered graph task, 3× LLM turns, completed 17:24:25Z.
  - **Phase 6a (STRANDED, decisive):** children completed DURING the pause → 2 reports sat in `report_injection`; after resume: root DB-status `running` but **message count frozen 25→25→25, zero `claim_for_injection`, zero PROCESS_REPORT, zero LLM activity, never completes** (sweeps t+2s/60s/3m). Only a NEW external message drains the buffer (proven 17:51, log 2727–2731).
- **Root cause:** pause path does not set a `resume_target_turn_id` handle (no SuspendTurn); resume therefore has nothing to dispatch, and it deletes the fired watchers that would otherwise wake the instance.

## B3 — Terminate mid-tree: DOWN works, UP absent (Phase 6b-mid)

- **Discovery:** the passing e2e (`test_terminate_after_spawn_then_revive`) uses **`DELETE /api/instances/{instance_id}`** (openapi: no params; `hard_delete=true` variant not used). `/stop` is broken (B5); `/api/jobs/cleanup` is nuclear (avoided).
- **Request:** `DELETE /api/instances/c83b46cd-…` (tester, mid-tree, grandchild mid-`sleep 480`) @ 17:55:53Z → `200 {"terminated":true}`.
- **DOWN propagation: ✅ WORKS** — grandchild `994369e9` graph cancelled mid-sleep (unwind 2ms, log 3084), cascade line 3094, tester trace `children=1` (3106). Developer sibling unaffected.
- **UP propagation: ❌ ABSENT** — root's watcher on the tester was **CANCELLED, not fired** (log 3101). Root's LLM still believes tester "⏳ STILL IN PROGRESS"; **log 3335: `waiting for 1 children (bus=True), deferring completion`** → root stuck `waiting_children` forever on a ghost child. Drift reconciler silent (`reconciled=0`).

## B4 — Terminate root: DOWN enumeration misses live children → orphan + livelock (Phase 6b-root)

- **Precondition note:** the stranded root (B2) had no wake source for a round-5 delegation, so developer was revived directly and was mid-`sleep 300` at terminate time.
- **Request:** `DELETE /api/instances/f5e223f1-…` @ 18:09:46Z → `200 {"terminated":true}`.
- **DOWN propagation: ❌ FAILED** — terminate trace `children=0` (log 3464), **no cascade line**: the LIVE RUNNING developer was missed entirely (child of a prior revive/churn round — transient `instance_hierarchy` enumeration ≠ permanent `instances.parent_id`). Developer orphaned ~6 min, completed naturally by luck.
- **Report-to-dead-parent aftermath (18:15:09):** orphan's completion report → `emit_terminal … no pending watchers` → `Buffered completion (no event yet)` → **new work row `d14cbde5` = `pending` forever** + **perpetual GUARD livelock**: `claim_pending_task … 1 eligible task(s) blocked by guard` repeating every ~3s, still active at final audit (18:20Z).

## B5 — `/stop` wrong-target defect (Phase 5)

- **Request:** `POST /api/instances/c83b46cd-…/stop` (tester) @ 17:32:35Z.
- **Response:** `200 {"paused":true,"paused_ids":["f5e223f1-…"],"skipped_ids":[]}` — **the ROOT, not the path-param instance.** Log 2349 confirms `Pausing instance f5e223f1` immediately after `POST …/c83b46cd…/stop` (2350).
- `/stop` is openapi-documented "Deprecated: Use POST /pause instead" — but unlike `/pause` (which respects the path param), it acts on the project root regardless. Side effect: it re-paused the root and set up the B2 phase-6a stranding.

## B6 — Detail endpoint 404 after resume (Phase 4)

`GET /api/instances/{id}` → 404 for ALL 5 tree instances throughout phase 4 (list endpoint + messages endpoint fine). In-memory manager state wiped by the resume cascade, never rehydrated. Breaks any client/agent relying on detail lookups post-resume.

## B7 — Work/job row integrity anomalies (Phases 3, 4, 6a)

- 3 work rows future-dated `2026-08-25T00:0x+00:00` (+7h — local(+07) clock stamped with UTC offset).
- `completed_at` of historical jobs re-stamped to the resume instant (observed twice).
- Jobs-detail said `completed` while jobs-list said `processing` for job `86b25d35` (phase 6a).

---

## Final system audit (Phase 6c, `phase6c-final-audit.json/.md`)

- Instances: **7/7 terminal-coherent** (by luck in developer's case — B4).
- Jobs: 4/4 coherent (86b25d35 cancelled/aborted).
- Work: 3/4 coherent; **1 dangling-pending (`d14cbde5`) + active GUARD livelock**.

## Trigger-condition matrix

| Condition | Outcome |
|---|---|
| Pause root, descendants busy | Only root pauses; descendants keep working (B1) |
| Resume root, children completed AFTER resume | Root recovers via live dep-bus watchers (accidental) |
| Resume root, children completed DURING pause | Root stranded `running` forever; buffered reports never delivered; only new message rescues (B2) |
| DELETE mid-tree child with live descendants | Children cancelled ✅; parent waits forever on ghost child (B3) |
| DELETE root with pre-churn live child | Cascade enumerates `children=0`; child orphaned; report to dead root → pending-forever row + 3s GUARD livelock (B4) |
| POST /stop on any non-root instance | ROOT gets paused (B5) |
| GET instance detail after resume | 404 until restart (B6) |

## Gaps / not measured

- `/stop` behavior on instances in projects with no leader root (single target assumed).
- Pause UP-propagation (mid-tree pause → root reaction) not directly isolated (superseded by B1 evidence that pause touches only the direct node).
- Whether `hard_delete=true` DELETE changes B3/B4 behavior.
- Live-rung/prod daemon NOT tested (dev only, by design).

## Live system state at handoff (intentionally preserved as evidence)

- Dev daemon alive: pid 12539 (uvicorn) / 12534 (dev.sh) — port 8079. **GUARD livelock still polling every ~3s** (work row `d14cbde5`).
- To clean: stop daemon via TERM on pid 12534→12539 (verify port 8079 first; **NEVER touch 8088**), or run the nuclear cleanup endpoint deliberately.
- Full log: `/tmp/pause-repro-20260824/dev-daemon.log` (line numbers cited above are absolute in this file).

## Evidence index

`/tmp/pause-repro-20260824/state.json` (master, 67KB, phase1–phase6 blocks) + `evidence/` (~130 files): `phase2-*` (tree build, 29), `phase3-*` (pause, 22), `phase4-*` (resume, 23), `phase5-*` (stop bug, 29), `phase6{a,b,c}-*` (55). Each claim above cites its phase evidence set; verbatim request/response bodies saved for every API call in every scenario.

## Workers

| Phase | Instance | Status |
|---|---|---|
| 1 boot | 4e95e423-323d-4ca1-86b8-f3056bd4a9ab | BOOTED |
| 2 tree | 15eb9982-483f-4e19-9801-738b4d6324fa | TREE_BUILT |
| 3 pause | 271035a0-9da1-439b-8814-40882410c906 | PAUSE_EXECUTED |
| 4 resume | 8b9c4223-661b-44d5-b511-79dbf48ef5b9 | RESUME_EXECUTED |
| 5 stop | f501820b-8743-487c-89c6-57ebf173faca | TERMINATE_FAILED_AS_TARGETED (→B5) |
| 6 final | c5a41487-e950-4712-b71f-08e11e8abd8c | A:STRANDED B:CASCADE-BUGS C:AUDITED |

No code changes, no commits, no pytest, no fixes applied (per mandate). Quarantine: N/A (no test suite run). ensure.md: N/A (no changed packs — investigation only).
