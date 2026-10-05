# Timeline — F-2 Demo E2E (UTC)

| Time | Event | Notes |
|------|-------|-------|
| 00:20:02 | START: Recorded port pids | 8079=v0.16.11(3567513), 9797=prod(3321986), 7979=demo(3457886) |
| 00:20:15 | Setup: data dir, wrapper, evidence dir | /home/nea/dev-daemon-8079-durability-f1f2/ |
| 00:21:03 | SIGTERM v0.16.11 dev daemon | pid 3567513, port 8079 freed |
| 00:21:15 | BOOT durability daemon (wrapper) | v0.16.13, factory engine 127.0.0.1:5432/ensemble_dev |
| 00:21:24 | RDRS service started | lanes=[deferred=T, no_row_backstop=T, pending_age=T, recovery_retry=T, orphan=T] |
| 00:23:15 | L1 attempt 1: parent created | parent=37305647 |
| 00:23:38 | L1 attempt 1: child spawned | child=b3c7dc12 |
| 00:24:37 | L1 attempt 1: parent completed (natural) | wedge window missed |
| 00:25:29 | L1 attempt 2: parent created | parent=3541055b |
| 00:25:43 | L1 attempt 2: child spawned | child=2feb0f5f |
| 00:26:39 | L1 attempt 2: KILL (wake in 'running' state) | wedge caught but F-1 wedge (not F-2) |
| 00:27:12 | REBOOT (lane 2 ON) | lane 2 ran, no per-row processing |
| 00:28:12 | L1 attempt 2: parent NOT healed (120s timeout) | F-1 preserved the in-flight task |
| 00:31:52 | L1 attempt 3: parent created | parent=3a739969 |
| 00:32:30 | L1 attempt 3: child spawned | child=aa43b52e |
| 00:32:53 | L1 attempt 3: parent completed (natural) | wedge window missed |
| 00:34:14 | L1 attempt 5: parent created | parent=f165c8f1 |
| 00:35:23 | L1 attempt 5: parent completed (natural) | poller exited too early |
| 00:35:35 | L1 attempt 5: retry with correct IDs | parent=a9a9a8be |
| 00:36:37 | L1 attempt 5: child=completed, wake_msg=ready | WEDGE CAUGHT at t=34838ms |
| 00:38:38 | L1 attempt 5: KILL | kill -TERM pid 369000 |
| 00:38:49 | REBOOT (lane 2 ON) | lane 2 ran, no per-row processing |
| 00:38:50 | WaitingChildrenWatchdog: wedge notice enqueued | parent woken by watchdog (not lane 2) |
| 00:39:01 | L1: parent LLM response: "Child completed and reported: helloL1" | PARTIAL PASS |
| 00:41:28 | L2: BOOT with lane 2 kill-switch OFF | lanes=[no_row_backstop=False] |
| 00:42:50 | L2: KILL (parent already completed) | wedge window missed |
| 00:43:14 | L2: REBOOT with lane 2 OFF | lanes=[no_row_backstop=False] |
| 00:44:08 | L2: V3 poller started | wedge not yet formed |
| 00:44:21 | ANOMALY: Daemon crash (DependencyBus bug) | RuntimeError for instance f165c8f1 |
| 00:45:52 | RESTART daemon (no extra env) | lane 2 back to default ON |
| 00:46:22 | L3: parent created | parent=96aa822e |
| 00:46:48 | L3: child spawned | child=e28d7bf1 |
| 00:47:03 | L3: KILL (child running) | mid-sleep kill |
| 00:47:17 | L3: REBOOT | AutoContinue candidates=1, scheduled=1 |
| 00:47:30 | L3: [BOOT_CONTINUE] instance=e28d7bf1 | child resumed from checkpoint |
| 00:48:01 | L3: LLM response: "helloL3" | PASS |
| 00:49:30 | L4: REBOOT 1 (run-twice) | lane 2 did 0 work |
| 00:49:30 | L4: REBOOT 2 (run-twice) | lane 2 did 0 work, counters unchanged |
| 00:50:07 | L5: parent created | parent=926fb34a |
| 00:50:30 | L5: 3 children spawned | children=b8310520, d973fff1, 291b7cfc |
| 00:51:20 | L5: all children completed | parent in waiting_children |
| 00:51:33 | L5: REBOOT | lane 2 ran, found delivery evidence, skipped |
| 00:52:00 | L5: parent completed | 3 report_injections rows, 0 wake_msgs |
| 00:52:30 | Evidence written | findings.md, recipe-pointer.md, timeline.md |
