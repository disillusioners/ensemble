# Timeline — F-2 Demo E2E G4 REDO (UTC)

## Pre-redo (from previous run, kept for context)
- 00:20:02 START: port pids recorded
- 00:21:03 v0.16.11 dev daemon SIGTERM'd
- 00:21:15 durability daemon booted (wrapper, v0.16.13)
- 00:23:15–00:51 L1-L5 legs (first run, adjudicated)
- 00:52 evidence committed at b24cab33
- 00:53 v0.16.11 dev daemon restored

## REDO run (2026-10-05)
- 00:55 R0 start: extract crash traceback
- 00:56 R0 verdict: PRE-EXISTING-AT-BASE (child_reports.py:2841 not touched by branch)
- 00:57 R0.5: clean state (22 instances deleted, 0 tasks, 0 msg_queue, 8088 UNBOUND)
- 00:59 R1: boot daemon, create parent c50a22ae + child 2bd3ea4e
- 01:00:57 R1 wedge captured at t=10566ms (SIGSTOP): child=completed, wake in 'ready', inj_state=PENDING
- 01:00:57 R1 SIGKILL sent
- 01:01:13 R1 reboot, lane 2 ran (found anchor, skipped)
- 01:01:22 watchdog started (interval=3600s)
- 01:03:50 R1 manual wake-up sent (violates criteria b, documented)
- 01:03:50+ R1 parent stuck (worker pool per-instance guard, wake in 'ready' not processed)
- 01:06:28 R1b reboot, parent completed via auto-continue (int_reports unchanged)
- 01:07:20 R2 boot with kill-switch OFF (lanes=[no_row_backstop=False])
- 01:08:30 R2 attempt 1: parent ce1d76f3 + child af0a2361, wedge missed (child took 36s)
- 01:10:26 R2 attempt 2 (R2r): parent ffac2a06 + child 399738fe, poller timed out (90s, child stuck)
- 01:13:04 R2 attempt 3 (R2F): parent b1bfa70a + child 1d444971, wedge missed (wake already completed)
- 01:14:48 R3 boot fresh, create parent 30993812 + 3 children
- 01:15:33 R3 poller on middle child, wedge missed (wake already completed)
- 01:16:23 R3 parent completed naturally
- 01:17:00+ R4: write findings.md, timeline.md, commit

## FORENSICS round (2026-10-05)
- 01:21 D1 prep: stop v0.16.11 dev (kill -9), boot durability daemon
- 01:22 D1 create parent c63eecab + child ec1a2dac
- 01:23:07 D1 wedge captured at t=26997ms (SIGSTOP): inj_state=PENDING, wake in 'ready'
- 01:23:07 D1 SIGKILL sent
- 01:23:30 D1 reboot ready
- 01:23:45 D1 t+30s forensics: parent waiting_children, wake ready, inj PENDING, no RDRS activity
- 01:23:56 D1 t+120s forensics: same (G1 sweep slowed captures)
- 01:27:06 D1 t+300s forensics: parent STILL STUCK, NEVER HEALED
- 01:27:20 D2 manual ping sent
- 01:27:20–01:29:50 D2 wait 150s: parent running, wake still ready, DEADLOCK
- 01:31:57 D3 boot with kill-switch OFF
- 01:32:09 D3 verified: lanes=[no_row_backstop=False]
- 01:32:30+ D5: write findings, commit, restore dev lane

## G4-r RE-GATE (2026-10-05)
- 04:40 R1r prep: kill v0.16.11 (kill -9), boot durability (lane 6 default ON verified: stuck_wake=True)
- 04:40 R1r create parent b5a5e621 + child b7c2a7c9
- 04:42:25 R1r wedge captured at t=34982ms (SIGSTOP): wake_task=running, worker_id=worker-4, inj_state=PENDING
- 04:42:25 R1r SIGKILL
- 04:42:38 R1r assertion saved
- 04:42:38 → 04:44:18 R1r wait 100s (heartbeat stale 183s > 90s threshold)
- 04:44:27 R1r reboot ready
- 04:44:27 R1r lane 6 ran (recover_on_startup) but found 0 candidates — QUERY BUG discovered
  (mq.message_id != ri.child_message_id in actual data shape)
- 04:45 R2r: verified NO env var for lane 6 in config.py
- 04:45 R2r: unit tests with lane_stuck_wake=False PASS (2/2)
- 04:48 R3r prep: boot durability, create parent 8b577fe3 + 2 children
- 04:50 R3r: child1 delivered naturally, poller missed child2's wedge, daemon crashed (pre-existing bug)
- 04:52 R4r: fresh parent 2acdf6b2 healed naturally, manual ping → normal response (no deadlock)
- 04:55 R5: write findings, commit, restore dev lane

## G4-r2 FINAL RE-GATE (2026-10-05)
- 05:42 LEG 1: kill v0.16.11, boot durability, create parent 344655ff + child 51ecc39b
- 05:44:19 LEG 1: wedge captured (SIGSTOP, wake_task=running, worker_id=worker-1, inj=PENDING)
- 05:44:19 LEG 1: SIGKILL
- 05:44:19 → 05:46:14 LEG 1: wait 100s (heartbeat stale 92s > 90s)
- 05:46:24 LEG 1: reboot, lane 6 heal PASS (recovered=1, task 1734→1735, same-message_id, retry_count=1)
- 05:46:24 LEG 1: injection recorded TASK_DELIVERED but parent STUCK
- 05:51:45 LEG 1: manual ping → parent completed (iter=17, ~34s)
- 05:55 LEG 2: create parent 1162418c + child1 50dcc93d (3s) + child2 afd68442 (60s)
- 05:56:48 LEG 2: child2 wedge captured
- 05:56:48 LEG 2: SIGKILL
- 05:56:48 → 05:58:14 LEG 2: wait 100s, heartbeat stale
- 05:58:15 LEG 2: reboot
- 05:59:18 LEG 2: lane 6 heal child2 ONLY (recovered=1, task 1746→1747)
- 05:59:18 LEG 2: parent stuck (injection gap)
- 06:00:30 LEG 2: manual ping → parent completed
- 06:02 LEG 3: create parent b0a7bd55 + child 0f5becb1
- 06:03:54 LEG 3: wedge captured
- 06:03:54 LEG 3: SIGKILL
- 06:03:54 → 06:06:00 LEG 3: wait 100s, heartbeat stale
- 06:06:00 LEG 3: boot with SERVICES_REPORT_DELIVERY_RECOVERY_LANE_STUCK_WAKE=false
  - Constructor log: stuck_wake=False
  - /proc/environ: env var =false
  - Wedge persists ≥120s (verified at t+0 and t+120s)
- 06:08:32 LEG 3: reboot WITHOUT override (default ON)
  - Constructor log: stuck_wake=True
  - Lane 6 heal: task 1751→1752, recovered=1
- 06:09:05 LEG 3: manual ping → parent completed
- 06:10+ R5: write findings, commit, restore dev lane

## LEG-1 FINAL Live Proof (Wake-Through) — 2026-10-05
- 07:09 LEG 1: kill v0.16.11, boot durability, create parent 5c791341 + child fb23ca4f (helloF4)
- 07:11:20 LEG 1: wedge captured (SIGSTOP, wake_task=running, worker_id=worker-2, inj=PENDING)
- 07:11:20 LEG 1: SIGKILL
- 07:11:20 → 07:13:10 LEG 1: wait 100s, heartbeat stale
- 07:13:24 LEG 1: reboot, lane 6 wake-through dispatch
  - sweep stuck_wake heal: task 1756 → retry 1757, recovered=1
  - _process_child_completion_and_notify_parent called: instance=fb23ca4f, message_id=334e080b
  - **IDEMPOTENCY GUARD FIRED**: child already completed → skip _process_child_completion_db_sync
  - **NO WAKE ROW CREATED** → parent STUCK
- 07:13:24 → 07:19 LEG 1: zero-ping observation (300s)
  - parent NEVER left waiting_children
  - api_msgs=0 (no manual pings)
  - inj_state: PENDING → TASK_DELIVERED at t+35s
  - retry 1757 at 07:14:25: "already delivered via report-injection (INJECTED) — skipping"
- 07:20:16 LEG 1: manual ping → parent completed (iter=16, ~32s) — R4ii STILL VERIFIED
- 07:21 LEG 1: FAIL verdict documented
- 07:22+ R5: commit, restore dev lane

## LEG-1 ROUND-5 (Ping-Seam Wake) — 2026-10-05
- 07:47 LEG 1: kill v0.16.11, boot durability @ 3c7c4df6, create parent 35ea11b6 + child 3cca8bf4 (helloF5)
- 07:48:53 LEG 1: wedge captured (SIGSTOP, wake_task=running, worker_id=worker-1, inj=PENDING)
- 07:48:53 LEG 1: SIGKILL
- 07:48:53 → 07:50:09 LEG 1: wait 100s, heartbeat stale
- 07:50:59 LEG 1: reboot, lane 6 heal (recovered=1, task 1761 → retry 1762)
- 07:50:59 LEG 1: ping-seam dispatch — [system:wedge-resolve] message enqueued to parent
- 07:50:59 LEG 1: parent auto-resumed WAITING_CHILDREN → RUNNING (instance_messaging.py:1968 seam)
- 07:50:59 → 07:51:26 LEG 1: parent LLM turn ran — synthesized helloF5 in final reply
- 07:51:26 LEG 1: pending-tasks guard deferred terminal transition (retry task 1762 still PENDING)
- 07:52+ LEG 1: retry task 1762 completed — but no event re-fired the terminal transition
- 07:51:26 → 08:13+ LEG 1: parent STUCK in running (not completed)
- 08:15 LEG 1: PARTIAL PASS verdict documented
- 08:16+ R5: commit, restore dev lane
