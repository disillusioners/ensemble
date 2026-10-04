# Phase 5: Demo E2E + evidence (AC8 — user-mandated, REAL environment)

## Objective

Prove the feature end-to-end on the REAL demo daemon (port 7979, `~/agents-ensemble-demo/`, NON-live target — restart explicitly sanctioned by the user): a leader-dev workflow with a long-sleep+say-hello child task mid-flight survives a mid-work daemon restart — the RUNNING child auto-continues from checkpoint with ZERO manual pings, the hello arrives, and the WAITING_CHILDREN parent is woken by the child's completion report (AC3 E2E leg) with no lost or duplicated reports. Evidence bundle captured (logs, timestamps, instance states before/after). The E2E runs implementation-branch code booted on demo.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 5.1 | Pre-flight: verify P4 gates green; deploy the feature branch to demo per the demo install's own release procedure (`~/agents-ensemble-demo/` — releases/ + `current` + `launcher.sh` observed on disk; CONFIRM the exact deploy+restart commands at execution time — drift rule, trap #3). Confirm demo `.env` does NOT set `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART=0` (default ON). Snapshot "before" state: instance list + statuses, tail of demo daemon log with boot marker | 4.x | Demo boots the feature branch; log shows `AutoContinue boot pass: ContinueResult(...)` line at boot; before-state captured to the evidence file |
| 5.2 | Scenario setup: dispatch a leader-dev workflow via the demo chat/API: the leader MUST (a) delegate ≥1 child task, and (b) the child's task = sleep ~120 s (bash `sleep 120`) THEN output "hello" (long-sleep+say-hello). Wait until observed state: parent instance `WAITING_CHILDREN` (or bus pending-count >0 for the parent — bus count is authoritative, status string cosmetic), child instance `RUNNING` mid-sleep | 5.1 | Observed + recorded: parent parked (WC), child RUNNING, child's Task row status='running', timestamped in evidence file. (If the leader resolves before the sleep ends, re-dispatch with a longer sleep — the state that matters is child RUNNING + parent WC simultaneously) |
| 5.3 | Restart the demo daemon MID-WORK (while the child sleeps): stop + start via the demo install's own mechanism. Record restart timestamps (t_stop, t_boot). Confirm the boot log shows: `AutoContinue boot pass` line with `candidates≥1`, one `[BOOT_CONTINUE]` line for the child instance, and NO `[BOOT_CONTINUE]` for the parent (WC skip) | 5.2 | Child auto-continued: no manual ping sent between t_stop and hello delivery; boot log evidence lines captured verbatim with timestamps |
| 5.4 | Verify the continued turn completes: the child finishes sleep+hello from CHECKPOINT (not a re-run from scratch — the sleep does not restart from 0; verify via message timestamps: total child wall-clock ≈ remaining sleep, and/or checkpoint-msg-count continuity in the `[RESUME] has_checkpoint` log line), the hello message arrives at the originating chat | 5.3 | Hello delivered; ZERO manual pings in the transcript between restart and hello; checkpoint-continuity evidence captured |
| 5.5 | AC3 E2E leg — child completion report wakes the parked parent: after the child's turn terminalizes, observe the parent: `dependency_watchers` FIRED → report enqueued → parent processes the PROCESS_REPORT task and resumes (bus-owned path, `dependency_bus.py:1499-1560` boot re-arm + durable fire). Parent wakes and produces its synthesis WITHOUT any manual message | 5.4 | Parent wakes from the child report alone; log evidence: bus start recovery line at boot + watcher fire + parent resume; timestamps show parent wake AFTER child terminal |
| 5.6 | No-lost/no-duplicated audit: count reports and turns — exactly ONE hello, exactly ONE child completion report processed by the parent, exactly one `[BOOT_CONTINUE]` for the child, zero `already_resuming` log lines, zero duplicate MessageQueue `internal_report:{child}:*` rows for the same msg, zero double parent wake | 5.5 | All counts exact; any deviation = FAIL with forensic capture (do not hand-wave; report actual) |
| 5.7 | Evidence bundle + report: write `.agents/tester/RESULTS/2026-MM-DD-auto-continue-demo-e2e.md` (implementation lane, tester conventions) with: before/after instance-state tables, restart timestamps, verbatim boot-pass log lines, hello-delivery proof, parent-wake proof, count audit, demo daemon version/commit (`git log -1` of the deployed release), pass/fail verdict per AC1/AC3/AC8. Link from the implementation PR description | 5.6 | Evidence file exists, complete, and every AC8 assertion has a timestamped artifact behind it |

## Scenario Outline (runnable)

```
# All commands executed against the DEMO install; confirm exact paths at run time.
# 0. Pre-flight
git -C ~/agents-ensemble-demo/current rev-parse --short HEAD   # == feature branch commit
grep ENSEMBLE_AUTO_CONTINUE /home/nea/agents-ensemble-demo/.env || echo "default ON"
# 1. Dispatch mission to a delegation-capable leader (demo chat or HTTP :7979):
#    "Delegate a child task: sleep 120 seconds, then say hello. Wait for it."
# 2. Poll until: parent WAITING_CHILDREN (bus count >0), child RUNNING, child mid-sleep
# 3. RESTART (user-sanctioned, NON-live):
#    <demo stop mechanism — confirm at run time; e.g. launcher/systemd unit>
#    <demo start mechanism>
# 4. Observe boot log: AutoContinue boot pass line + [BOOT_CONTINUE] child only
# 5. Wait (NO interaction): child hello arrives ≈ (remaining sleep + LLM latency)
# 6. Observe parent wake from child completion report (bus path)
# 7. Audit counts; capture evidence bundle
```

## Evidence-Capture Checklist

- [ ] Demo deployed commit hash (`git -C ~/agents-ensemble-demo/current log -1 --oneline`)
- [ ] Boot log: `AutoContinue boot pass: ContinueResult(candidates=N, scheduled=N, ...)` (t_boot)
- [ ] Boot log: exactly one `[BOOT_CONTINUE] instance=<child id8>` line; zero for the parent
- [ ] Pre-restart state: parent WC (or bus count >0), child RUNNING, Task row `status='running'`
- [ ] Post-restart: `[RESUME] instance=<child id8> has_checkpoint=True` line
- [ ] Hello message delivered (chat transcript screenshot/log excerpt + timestamp)
- [ ] Zero outbound manual messages between t_stop and hello (transcript audit)
- [ ] Bus lines: boot `bus start: warmed=... recovered=... swept=...` + parent wake/fire lines
- [ ] Count audit table (hellos=1, child reports processed=1, BOOT_CONTINUE=1, already_resuming=0)
- [ ] t_stop / t_boot / hello-timestamp / parent-wake-timestamp timeline
- [ ] Verdict lines per AC1 / AC3 / AC8 (explicit PASS/FAIL each, with artifact reference)

## Coupling

- **Depends on P1-P4 complete** (feature deployed + gates green).
- **Black-box + logs** — no new test code; exercises the REAL stack (LangGraph checkpoint, PG/SQLite on demo, bus, wake lanes).
- **AC3 leg** — 5.5 is the mandatory E2E proof that the bus-owned WC wake survives restart with the feature shipping alongside it.

## Risks

- **Scenario timing**: the leader may resolve before the restart window. Mitigation: sleep duration 120 s gives a comfortable window; poll state before restarting; re-dispatch on miss.
- **Demo deploy mechanics drift**: the demo install's stop/start path must be confirmed at execution time (trap #3). Mitigation: inspect `launcher.sh` + releases layout first; never guess.
- **Restart during the wrong phase**: restarting during the LEADER's own turn (not the child's) tests the parent-continue leg instead. Mitigation: poll for child-RUNNING + parent-WC BEFORE t_stop; if the leader itself is RUNNING, that is ALSO a valid AC1 observation — record which leg fired.
- **Demo data safety**: demo is NON-live; still, capture a pre-run DB copy (`cp` the demo data dir) so the audit can be re-queried without re-running.

## AC Mapping

- **AC8** — the entire phase (user-mandated real-environment proof).
- **AC3** — 5.5 (parent wakes from child report after restart; bus-owned path proven in production shape).
- **AC1** — 5.3/5.4 (RUNNING auto-continue; PAUSED untouched is unit-pinned in P3 and observable here if any PAUSED instance exists on demo — record its state as unchanged).
- **AC5** — 5.6 (no duplicates across a real restart).
- **AC6** — implicit: boot completed and listener came up with the pass in the lifespan (boot log ordering).

## Rollback Note

If the E2E fails: capture evidence, DO NOT iterate blindly — triage against the risk-register (R1 double-fire, R2 boot wedge, R5 claim-guard regression). Redeploy demo from its previous release (demo releases/ layout supports rollback) and re-run after a fix. The demo DB copy from 5-risks supports post-mortem queries.
