# Runbook: Post-Restart Arm-Notify

Operator-facing entry point for the post-restart arm-notify feature: after a
daemon restart that follows an armed `system_restart` / `system_upgrade`, the
arming instance is woken automatically with a self-describing message so it
checks the run outcome and reports back to the user in the same chat — zero
user action. This closes the D-FA1.2 pull-model supersession: the arm-time
banner no longer says "ask me to run `upgrade_status`"; the push-wake does
the equivalent on boot.

ADR basis (all six are binding design records in
`.agents/shared/planning/post-restart-arm-notify/decisions.md`):
**ADR-039** (wake record schema + write atomicity — `pending_wakes` keyed by
`run_id` on `releases/state.json`, written inside the caller-acquired
journal lock), **ADR-040** (boot sweep + wake delivery primitive —
`UpgradeJournalSweepService.sweep_wake_records` on the boot pass + the 90s
tick, delivered via `manager.enqueue_message`), **ADR-041** (routing
preservation — the wake's `source` is the recorded arm-time user-origin
source), **ADR-042** (terminal-state gating — wake only on a terminal-class
journal history event after `armed_at`), **ADR-043** (edge cases &
coalescing — front-door ari fall-back, coalesce by `arming_instance_id`,
bounded at `PENDING_WAKE_COALESCE_MAX=16`, one-shot delivery), **ADR-044**
(kill-switch & boot-never-wedge). Architecture analysis:
`architecture-recommendation.md` §FA1–§FA6 (invariants 8–11); supersession `supersession-record.md` §4 (D-FA1.2).

## 1. Overview

- **Arm:** `system_restart` / `system_upgrade` (dry_run=false) write the
  `pending_op` AND the `pending_wakes` record in the SAME journal-write
  envelope under the caller-acquired lock (ADR-039; the call site is pinned
  inside the lock-holding `try` — torn-write regression pin T6.4). The
  arm-time banner documents the auto-wake + kill-switch (regression pin:
  `tests/unit/tools/test_post_restart_arm_notify_banner.py`).
- **Deliver:** on the boot pass and every 90s tick, `sweep_wake_records`
  walks `pending_wakes`, gates each record on a terminal-class history
  event in the `armed_at` window (ADR-042), coalesces per arming instance
  (ADR-043), re-stamps the user-origin window (ADR-041), and enqueues the
  wake to the arming instance. Delivery is one-shot: the record is
  structurally removed from the dict on `delivered`/`abandoned`.
- **Fallback:** missing arming instance → front-door `ari` gets an
  annotated wake; no ari → `arm_notify_no_instance` journal event + the
  wake is abandoned (`reason=instance_missing`) (ADR-043).
- **Live never records (invariant 8):** `system_restart` outright-refuses
  on live BEFORE any journal write; `system_upgrade` on live only ever
  reaches the verified-arm path (3-factor gate) — a live-arming wake is
  structurally impossible (ADR-044 / D-FA5.5).

## 2. Operator kill-switch

`ENSEMBLE_POST_RESTART_ARM_NOTIFY=0` (default ON) disables the feature —
both the arm-side write and the sweep-side delivery read the SAME env var,
per call, so one flip covers everything (ADR-044):

1. Add `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0` to `<install_dir>/.env`.
2. Restart the daemon (the launcher stages `.env` into the process env at
   boot; per-call reads then honor it on every arm and tick — no further
   restarts).
3. Effects while OFF: new arms write NO wake record (the arm is
   unaffected); pending records are abandoned on the first tick by the
   one-time abandon-on-switch-off pass, each journaling
   `wake_abandoned` with `reason=kill_switch_off`.
4. Re-enable: remove the line (or set `=1`) and restart. Abandoned records
   are terminal and never resurrected — no stale-flood of old wakes.

## 3. Structured log lines

Logger lines (grep the daemon log):
| Event | Log line | Meaning |
|---|---|---|
| boot sweep result | `UpgradeJournalSweepService wake boot sweep: WakeSweepResult(pending_at_start=N, ..., delivered=D, ...)` | per-boot wake pass outcome (api.py lifespan) |
| enqueue failure | `UpgradeJournalSweepService: enqueue_message FAILED for run_id=<id>` | wake held pending; retried next tick (boot-never-wedge) |
| arm-side write failure | `arm_pending_wake failed for run_id=<id>` | arm continued WITHOUT a wake (documented degraded mode) |
| ari fall-back miss | `UpgradeJournalSweepService: ari fall-back: zero active ari instances for project=<pid>` | no delivery target; see Recovery Flow |

Durable journal history events (in `<install_dir>/releases/state.json`
`history[]` — the structured record of record; survives restarts):

| Event | Meaning |
|---|---|
| `wake_abandoned` (detail carries `reason=kill_switch_off`, `reason=grace_expired`, or `reason=instance_missing`) | record transitioned to abandoned; removed from `pending_wakes`. `grace_expired` is the automatic grace-window sweep (R-5 / R-21); `kill_switch_off` is the operator kill-switch one-time pass; `instance_missing` is the ari fall-back failure. |
| `arm_notify_no_instance` | wake could not be delivered anywhere (no arming instance, no ari) |
| `wake_coalesce_overflow` | wakes past the coalesce cap (16) were dropped — pull model covers them |

Grep recipes:

```bash
# wake-sweep outcomes from the daemon log (<install_dir>/data/logs/ensemble.log)
grep 'wake boot sweep' <install_dir>/data/logs/ensemble.log | tail
# abandoned wakes + reasons from the journal
python3 -c "import json;h=json.load(open('<install_dir>/releases/state.json'))['history'];print(*[e for e in h if e['event'].startswith('wake') or e['event']=='arm_notify_no_instance'],sep='\n')"
```

## 4. Recovery Flow

**4.1 Universal pull model (works in EVERY case below).** The interactive
query is always available: ask the instance (or the project's front-door
ari) to run `upgrade_status(run_id="<id>")` — or `upgrade_status()` with no
args to enumerate. The journal's terminal state survives restarts; the
pull query is the recovery of record (D-FA1.2 fall-back).

**4.2 Chat adapter disabled/unavailable at wake time (R-24).** The
`MessageQueue` row is created but dispatch silently drops it
(`system:*` source or transport error): from the daemon's view the wake is
`delivered`, but the user never saw the report. **Recovery:** the 4.1 pull
query. The one-shot loss window is an accepted, pinned trade-off (ADR-040
addendum); a dispatch-success callback alternative was rejected (parallel
machinery, AC6).

**4.3 Delivered-mark vs dispatch-failure window (R-25).** `mark_wake_delivered`
fires when `enqueue_message` returns a `message_id` — BEFORE adapter-level
dispatch — so an adapter failure in that window loses the report with the
wake already marked delivered. Same recovery: the 4.1 pull query. Same
accepted trade-off (ADR-040 addendum); no retry lever exists because the
record is already gone from `pending_wakes`.

**4.4 Executor-never-ran / launcher burst-abort (R-21).** If the pipeline
never journals a terminal event (executor never spawned, launcher abort
exit-1, daemon died mid-write before the record existed), the wake
**abandons automatically at the grace window**: the sweep compares
the wake's ``abandon_after`` (= ``expires_at + PENDING_WAKE_GRACE_S``,
default ``expires_at + 600s``) against the current time on every
tick; past the grace the record is removed and a ``wake_abandoned``
history event is journaled with ``reason="grace_expired"``. The grace
pass runs BEFORE the kill-switch OFF pass so a past-grace record
gets the more-specific ``grace_expired`` label (never
``kill_switch_off``). Records within grace remain pending
indefinitely. **Recovery:** the 4.1 pull query for the armed
``run_id``; daemon-down itself is watchdog territory (ADR-025(b),
complementary). Operator cleanup of a still-pending record: flip
the kill-switch OFF → one restart → the abandon-on-switch-off pass
clears it (``reason=kill_switch_off``) → flip back.

**4.5 Missing arming instance.** Terminated/expired arming instance → the
wake falls back to the project's front-door `ari` (annotated body);
no active ari → `arm_notify_no_instance` history event + the wake is
abandoned (`reason=instance_missing`). **Recovery:** create/start an ari
instance for the project, then run the 4.1 pull query there.

**4.6 Kill-switch off pass.** Records present while OFF are abandoned
(`reason=kill_switch_off`) on the first tick — the user gets no wake.
Recovery is the 4.1 pull query; re-arming after re-enable works normally
(no stale state).

## 5. Drill

```bash
bash test/drills/post_restart_arm_notify_drill.sh
```

Six sandbox scenarios (arm+restart+observe, upgrade outcome, source routing,
coalesce, kill-switch, live-outright-refusal via the FAKE-live marker —
never the real live install). Structured `PASS:`/`FAIL:` log per scenario;
**exit 0 = green** (the operator's acceptance signal). Companion drill
runbook: `docs/runbooks/upgrade-drills.md` (the P2.1 pipeline drill).
