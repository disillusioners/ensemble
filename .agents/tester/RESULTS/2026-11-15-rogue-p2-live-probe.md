# rogue-containment-p2 tester gate — Gate 6 live-probe correction evidence (raw text landed verbatim)

ROGUE-CONTAINMENT-P2 G6 LIVE-PROBE CORRECTION — LISTENER-ONLY IDENTITY EVIDENCE
===============================================================================
Purpose: close anomaly c4d8cc2c as a probe artifact by recording CORRECT
listener-identity evidence (prior lane used pgrep -f / ss client rows and
produced a false "live churn" alarm).

Method constraints honored:
- Identity via ONLY: (1) `systemctl show -p MainPID -p ActiveState -p SubState
  ensemble-main.service` and (2) `ss -tlnp 'sport = :<port>'` for 9797/9798/7979.
- NO pgrep -f, NO `ps aux | grep`, NO ss -tnp client rows, NO process-list greps.
- Read-only: no kill, no systemctl restart/start/stop, no writes in the repo or
  live install, no DB contact. Only artifact written: this /tmp file.
- Dwell between reads: 230 s (requirement >= 180 s).

=== READ-1 @ 2026-11-15T00:31:16Z (UTC) ===
$ systemctl show -p MainPID -p ActiveState -p SubState ensemble-main.service
MainPID=4119088
ActiveState=active
SubState=running

$ ss -tlnp 'sport = :9797'
LISTEN 0  2048  0.0.0.0:9797  0.0.0.0:*  users:(("ss",pid=413491,fd=6),("sh",pid=413486,fd=6),("npm exec @upsta",pid=393911,fd=6),("MainThread",pid=305835,fd=6),("ensemble-prod",pid=305455,fd=6))

$ ss -tlnp 'sport = :9798'
LISTEN 0  5  127.0.0.1:9798  0.0.0.0:*  users:(("ensemble-prod",pid=305455,fd=31))

$ ss -tlnp 'sport = :7979'
LISTEN 0  2048  127.0.0.1:7979  0.0.0.0:*  users:(("ensemble-prod",pid=304612,fd=27))

=== READ-2 @ 2026-11-15T00:35:06Z (UTC) — after 230 s dwell ===
$ systemctl show -p MainPID -p ActiveState -p SubState ensemble-main.service
MainPID=4119088
ActiveState=active
SubState=running

$ ss -tlnp 'sport = :9797'
LISTEN 0  2048  0.0.0.0:9797  0.0.0.0:*  users:(("ss",pid=416060,fd=6),("sh",pid=414074,fd=6),("npm exec @upsta",pid=413927,fd=6),("MainThread",pid=305835,fd=6),("ensemble-prod",pid=305455,fd=6))

$ ss -tlnp 'sport = :9798'
LISTEN 0  5  127.0.0.1:9798  0.0.0.0:*  users:(("ensemble-prod",pid=305455,fd=31))

$ ss -tlnp 'sport = :7979'
LISTEN 0  2048  127.0.0.1:7979  0.0.0.0:*  users:(("ensemble-prod",pid=304612,fd=27))

=== STABILITY VERDICT: STABLE — single stable generation ===
- MainPID: 4119088 in BOTH reads (stable). ActiveState/SubState: active/running both.
- :9797 actual binder: ensemble-prod pid=305455 in BOTH reads (stable). This pid
  EQUALS the audit-saw value 305455 — ZERO generation change; no churn, no alarm.
- :9798 binder: ensemble-prod pid=305455 in BOTH reads (stable).
- :7979 binder: ensemble-prod pid=304612 in BOTH reads (stable; a distinct second
  ensemble-prod process owning its own listener — stable across reads, unalarming).
- No port newly bound or unbound among the three across reads.
- Pids that DID change on the :9797 row between reads are FD-INHERITED aliases,
  not binders: the probe's own ss/sh (413491/413486 -> 416060/414074) and an
  npm-exec MCP child (393911 -> 413927) hold inherited fd=6 on the live listener
  (documented FD-INHERITANCE PORT CAPTURE class). Their churn is expected noise
  and demonstrates exactly the artifact class that produced the original alarm.

=== MainPID vs 9797 listener owner (cross-check) ===
MainPID (4119088) does NOT literally equal the 9797 binder pid. Consistent with
the unit contract (ExecStart=launcher.sh, KillMode=mixed): MainPID is the
launcher wrapper process; the actual port binder is its ensemble-prod child
(pid=305455). Since the binder pid IS the audit-saw value 305455, the daemon
generation the audit observed is still live — recorded as structure, NOT a
generation change, and NOT alarmed.

=== PRIOR PROBE METHOD — ARTIFACT-PRONE ===
Per POK pid-numeric-band-not-identity: pgrep -f and ss -tnp client rows prove
nothing about daemon identity — numeric pid bands move and client rows surface
fd-inherited transients. The prior lane's "live churn" alarm (c4d8cc2c) is
closed as a probe artifact. Correct identity anchors: MainPID/launcher structure
plus the listener binder pid — both STABLE across this probe.
