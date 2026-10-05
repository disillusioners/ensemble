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
