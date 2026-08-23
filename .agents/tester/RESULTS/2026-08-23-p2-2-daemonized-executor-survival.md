# P2.2 Dispatch-B — Daemonized-Executor Survival Across Daemon SIGKILL (M5 evidence)

- **Date:** 2026-08-23 · **Branch:** `feature/self-restart-p2p2-ari-tools` @ `c96c0f0d` + fix-pass working tree
- **Provenance (honest note):** this is a **REGENERATED equivalent** of the Dispatch-B
  sandbox drill (2026-08-23), re-run during the independent-reviewer fix pass because
  the original session transcript was not committed to the tree. Same real seam, same
  assertions, fresh tmp sandbox (`/tmp/p22-m5-drill`), this machine, this date. No live,
  demo, or prod path touched — fixture parent + fixture child only.
- **What is exercised:** the REAL `daemon.tools.upgrade_journal.spawn_executor`
  (`subprocess.Popen(..., start_new_session=True)` ≡ double-fork + setsid on macOS /
  PyInstaller, ADR-029): a fixture "daemon" (parent) spawns the executor payload through
  the real seam, the parent is then SIGKILLed, and the child must (a) survive, (b) be its
  own process-group leader (no group-kill coupling), (c) keep making progress, (d) have
  its stdio in `data/upgrade.log`.

## Drill transcript (verbatim)

```text
=== M5 drill: daemonized-executor survival across parent SIGKILL ===
date: 2026-08-23T10:35:09Z

--- step 1: launch fixture daemon (real spawn_executor, start_new_session) ---
parent pid (file): 81353   child pid: 81378
81353 81349 81346      0 /opt/homebrew/Cellar/python@3.14/.../MacOS/Python parent_fixture.py /tmp/p22-m5-drill/install
81378 81353 81378      0 /bin/bash -c for i in $(seq 1 60); do printf "beat %s\n" "$i" >> .../child-heartbeat; sleep 0.5; done; ...
parent pgid=81346  child pgid=81378
SESSION-CHECK: child is its OWN process-group leader (detached) — OK

--- step 2: SIGKILL the parent (daemon death simulation) ---
parent dead (SIGKILL confirmed)

--- step 3: child survival + progress across parent death ---
child ALIVE after parent SIGKILL — SURVIVAL OK
heartbeat lines before-wait=5 after-wait=9 (still progressing: YES)
81378     1 81378 /bin/bash -c ...   ← reparented to PID 1, own pgid intact

--- step 4: teardown fixture child (drill hygiene; real executor would exit on its own) ---
child reaped

--- step 5: executor log placement (stdio -> data/upgrade.log) ---
-rw-r--r--  1 nguyenminhkha  wheel  0 Aug 23 17:35 install/data/upgrade.log
upgrade.log present — OK

VERDICT: SURVIVAL OK + OWN-PG OK + LOG OK = drill PASS
```

## Assertions behind each OK

| # | Assertion | Evidence above |
|---|---|---|
| 1 | Child spawned via the REAL `spawn_executor` seam (not a re-implementation) | fixture parent imports `daemon.tools.upgrade_journal.spawn_executor` and calls it |
| 2 | Child is its own process-group leader (`start_new_session` ≡ setsid) | child pgid (81378) == child pid (81378) ≠ parent pgid (81346) |
| 3 | Child survives parent SIGKILL | alive after `kill -9` of parent; `ps` shows ppid repointed to 1 |
| 4 | Child keeps progressing (not a zombie) | heartbeat file grows 5 → 9 lines across the wait window |
| 5 | stdio routed to `data/upgrade.log` under the install dir | step 5 `ls` |

This complements the committed unit-level pins (pack `upgrade_tool_interlock_unit_test`:
env allowlist, process-group independence, no-BashProcessRegistry AST pin) with a live
process-tree demonstration on a real macOS host.

## Scope note — T7–T9 e2e drills are P2.3 DR-5 scope

The full end-to-end drills (T7 restart e2e, T8 upgrade e2e, T9 crash-window e2e — real
stop→start→gate cycles against sandbox installs with a real daemon) are **P2.3 DR-5
scope** per the phase plan; this M5 entry covers ONLY the executor-survival assumption
(assumption #2 of the pre-freeze checklist — CLOSED). Recorded here so the fix-pass
reviewer sees the survival evidence without waiting for DR-5.
