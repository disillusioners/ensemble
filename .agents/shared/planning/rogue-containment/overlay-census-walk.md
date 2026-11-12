# Overlay (pkill) guard — 16-lane sanctioned-lane census walk

Rogue-containment component A/B (iter-1 hardened pattern set). For each
sanctioned lane, re-inspected actual kill/pkill/killall/fuser/systemctl
usage at tip 715f4eedc+ against the NEW pattern set and recorded
PASS / FENCE / BLOCKED. Pattern set is FIXED; do NOT widen or narrow to
make the walk pass.

NEW pattern set (iter-1 D2a/D2c):
  live name tokens: ensemble-prod, ensemble-daemon, ensemble-main,
                    ensemble-demo, agents-ensemble
  port tokens (word-boundary): \b9797\b, \b9798\b
  kill-like verbs: pkill, killall, fuser, kill (command word only),
                   xargs kill, systemctl (mutating verbs only)
  systemctl mutating (gated): stop restart kill disable mask reload
    daemon-reload enable start try-restart isolate
  systemctl read-only (allowed): status show is-active list-units
    list-unit-files cat is-enabled is-failed get-default help
  false-positive guards: "skill " (no trigger), "19797" (no trigger)

| # | Lane | Verdict | Notes |
|---|------|---------|-------|
| 1 | Live boot + promote hand-over | PASS | No kill/pkill targeting live patterns |
| 2 | Promote/restart/rollback chain (promote.sh, adopt-unit.sh) | PASS | PORT read from staged .env only |
| 3 | dev.sh boots on 8079 | PASS | binds 8079, no live-pattern kill |
| 4 | Pytest transient-unit boots (wedge_smoke) | PASS | scratch ports, no live-pattern kill |
| 5 | Tester staged-binary scratch mirror (boot_v2.sh) | FENCE | carries ENSEMBLE_FENCED_TEST_CONTEXT? boot_v2.sh does not set it; if it ever runs a kill-like cmd against live patterns it must set the fence. No such cmd observed at tip => effectively PASS; fence point documented. |
| 6 | DR-2 exit-78 drill | PASS | port 8377, no live-pattern kill |
| 7 | scripts/test_builtin_mcp_servers.sh (PORT=18088) | PASS | no kill-like cmd |
| 8 | Shell packs sourcing launcher.sh (run_tests_scrubbed.sh) | PASS | scrubbed env, no live-pattern kill |
| 9 | Soak/pack fences (dbpool_gate_ladder.sh, soak_gate_mcp_warmup_acceptance.sh) | PASS | per-var fence |
| 10 | Live boot (tier-1) | PASS | no kill cmd |
| 11 | Stage.sh no-boot | PASS | no daemon boot |
| 12 | Manual PG smoke 8199 | PASS | disposable PG |
| 13 | Deploy.sh | PASS | ownership-scoped stop, not port-based |
| 14 | Makefile install | PASS | no live contact |
| 15 | Frozen entry points (run_app, run_tests_scrubbed) | PASS | no kill cmd |
| 16 | Interpreter-certification fences | PASS | per-var scrub |

Result (re-walked against iter-1 pattern set): 15 PASS, 1 FENCE (boot_v2.sh — fence available via env overlay/ambient; not BLOCKED; boot_v2.sh now also exports ENSEMBLE_DB_AMBIENT_ALLOW=1 for its sanctioned postgres boot). No BLOCKED lanes. Fallback contract not triggered; no pin-i drop required. All kill-like commands in the lane scripts target scratch PIDs/ports (18088, \$DPID, \$CHILD_PID) — none match a live pattern.
