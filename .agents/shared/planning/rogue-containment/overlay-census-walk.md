# Overlay (pkill) guard — 16-lane sanctioned-lane census walk

Rogue-containment component A/B. **Fix-round 2 re-walk (2026-11-12)** after the
fence channel changed (B6) and the evasion surface was fully documented (N10).

## Fence channel (changed in fix-round 2 / B6)

The fence is **AMBIENT-ONLY**: `ENSEMBLE_FENCED_TEST_CONTEXT=1` must be set in
the **daemon-set ambient environment** (`os.environ`) — e.g. exported by the
mirror harness before the daemon starts.

The tool-call env overlay (`bash(env={"ENSEMBLE_FENCED_TEST_CONTEXT": "1"})`)
is **NOT** a fence channel any more: the pure predicate ignores it, and the
bash tool strips the key from the child environment (warning logged). Before
the fix an agent could grant itself a fence in the same call that issued a
live-pattern kill — the guard was pattern-only, not containment.

**Migration:** a lane that used the overlay channel must move the export into
the daemon/harness ambient env. Re-walk result: **no lane used the overlay
channel** (repo-wide grep: the marker appears only in the guard + its pins),
so no lane requires migration. Any future mirror sets it per-boot, ambient.

## Lane table (re-walked at the fix-round-2 tip)

| # | Lane | Verdict | Notes (re-walked) |
|---|------|---------|-------------------|
| 1 | Live boot + promote hand-over | PASS | No kill/pkill targeting live patterns |
| 2 | Promote/restart/rollback chain (promote.sh, adopt-unit.sh) | PASS | `systemctl` calls are comments/guarded; PORT read from staged .env only |
| 3 | dev.sh boots on 8079 | PASS | static pin: zero executable pkill/killall in dev.sh (`tests/test_dev_sh_exit_trap_scope.sh`); exit trap kills by exact PID |
| 4 | Pytest transient-unit boots (wedge_smoke) | PASS | scratch ports/PIDs; no live-pattern kill |
| 5 | Tester staged-binary scratch mirror (boot_v2.sh) | PASS | No live-pattern kill observed. If one is ever needed, set `ENSEMBLE_FENCED_TEST_CONTEXT=1` **ambiently before the daemon starts** (overlay is no longer a channel) |
| 6 | DR-2 exit-78 drill | PASS | port 8377, no live-pattern kill |
| 7 | scripts/test_builtin_mcp_servers.sh (PORT=18088) | PASS | `lsof -ti:$TEST_PORT \| xargs kill -9` targets a scratch port — and is the canonical **two-step PID-laundering** shape the pattern guard does NOT catch (residual R1 below) |
| 8 | Shell packs sourcing launcher.sh (run_tests_scrubbed.sh) | PASS | scrubbed env, no live-pattern kill |
| 9 | Soak/pack fences (dbpool_gate_ladder.sh, soak_gate_mcp_warmup_acceptance.sh) | PASS | per-var fence |
| 10 | Live boot (tier-1) | PASS | no kill cmd |
| 11 | Stage.sh no-boot | PASS | no daemon boot |
| 12 | Manual PG smoke 8199 | PASS | disposable PG |
| 13 | Deploy.sh | PASS | ownership-scoped stop, not port-based |
| 14 | Makefile install | PASS | no live contact |
| 15 | Frozen entry points (run_app, run_tests_scrubbed) | PASS | no kill cmd |
| 16 | Interpreter-certification fences | PASS | per-var scrub |

Result: **16 PASS, 0 FENCE, 0 BLOCKED.** No lane is fenced via the tool overlay;
the fallback contract is not triggered and no pin was dropped. All kill-like
commands in the lanes target scratch PIDs/ports (`18088`, `$DPID`,
`$CHILD_PID`, `supervision_stub_daemon.py`, `$WORK/`) — none contain a live
pattern token.

## Residuals / evasion surface (N10 — documented, NOT closed by the pattern guard)

The overlay is **defense-in-depth only**. It cannot contain a determined
same-uid agent; the OWNERSHIP CLAIM (component A) is the control that does not
depend on command text. Known shapes:

1. **Two-step PID laundering** — resolve the PID with a read-only command
   (`lsof -ti:9797`, `pgrep -f ensemble-prod`) and then `kill <pid>`; the second
   command contains no live token. (Lane 7 above is this exact shape on a
   scratch port.)
2. **Regex forms** — `pkill -f 'ensemble.*prod'`, `pkill -f 'ensemb[l]e-prod'`:
   the pattern token is not present verbatim.
3. **Substring collisions** — `ensemble-maintenance` contains
   `ensemble-main`, so the guard OVER-refuses such a command (conservative
   failure is accepted; it never under-refuses a literal token).
4. **`kill -9 -1`** — signal every process the uid may signal; no token.
5. **Same-UID limits** — another process as the same uid can unlink claims,
   signal peers, or read the claims dir (0700 per-uid is the boundary).
6. **Same-install H1** — a rogue executing the same install's binary is
   non-foreign to the ownership claim (declared hole, analysis §8 H1).
7. **Wrapper indirection** — `sh -c "$CMD"` where `$CMD` is assembled at
   runtime from pieces below the guard's visibility.

Escalation for any of these = the process-ownership lane (pkill guard is not
the boundary).
