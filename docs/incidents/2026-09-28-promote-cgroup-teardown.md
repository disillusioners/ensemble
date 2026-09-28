# Promote/Upgrade Cgroup-Teardown Incident — 2026-09-28

**Date:** 2026-09-28
**Incident run:** `r-20260928-005506-f82e` (first agent-gated self-upgrade promote on
ensemble-vm LIVE, v0.16.0 → v0.16.1, human-confirmed via Discord nonce)
**Branch:** `fix/promote-cgroup-survivorship` (base `84869379` = v0.16.1)
**Status:** Code fix shipped; live install recovery performed by devops 2026-09-28
(see live-install doc referenced below).
**Source of truth (READ-ONLY):** `/home/nea/agents-ensemble/docs/2026-09-28-ensemble-selfupgrade-cgroup-incident.md`
— the live install's authoritative forensic record. This file is a
**dev-repo summary** — the live doc stays authoritative.

> **Root cause in one line:** the promote executor was spawned with
> `start_new_session=True` (≡ `setsid`) — a setsid creates a new SESSION,
> it does **NOT** leave the systemd unit's cgroup. When
> `ensemble-live.service` deactivated under `KillMode=control-group`,
> the cgroup teardown SIGTERMed the executor **before the symlink flip**
> could run. Survivorship was designed on macOS launchd, where a setsid
> child is not killed when its parent exits.

---

## 1. Timeline (UTC, 2026-09-28)

| Time | Event |
|------|-------|
| 00:55:06 | Promote run starts (v0.16.0 → v0.16.1, human-confirmed) |
| ~00:56:51 | `UpgradeJournalSweepService` (in-daemon reaper) stops with the daemon |
| 00:56:53 | `stop-ensemble.sh` SIGTERMs the launcher (ADR-009 single-TERM); daemon drains cleanly (0.71s, 134/134 jobs, no data loss) |
| 00:56:53.380 | Launcher exits 143 → unit deactivates |
| 00:56:53.397 | Cgroup teardown SIGTERMs the executor pid (2s into 70s bounded wait); unit "Deactivated successfully" 17ms after launcher exit |
| 00:56:53 | **OUTAGE** — www.ensem.dev down (9797 not serving) |
| ~01:26:22 | Manual recovery (Option B, outside the cgroup) → v0.16.1 serving, journal reconciled |

**Impact:** ~29.5 minutes downtime. Zero data loss.

## 2. Failure signature (for future recognition)

A failed promote in this class looks like:

- `ensemble-live.service` **inactive, NOT failed** (`NRestarts=0`, "Deactivated successfully")
- `releases/state.json`: `in_flight` non-null + `pending_op` non-null, **`flipped:false`**
- `current` symlink still pointing at the OLD release
- **No** executor log in `/tmp`
- **No** OOM / kernel kill — executor death is a plain SIGTERM from cgroup teardown
- Service not answering on :PORT

## 3. Root cause (confirmed by recon)

- `spawn_executor()` launches the executor with `start_new_session=True` (≡ setsid).
  `setsid` creates a new session — it does NOT leave the unit's cgroup.
- When the unit deactivated, `KillMode=control-group` SIGTERMed **every process in
  the unit's cgroup** — including the executor.
- Secondary gap: the in-daemon reaper died BEFORE the executor, so the upgrade
  transaction was orphaned with **no terminal event** — nothing on the host
  reconciles a stuck `in_flight` state.
- Survivorship model (`executor must survive daemon death`) is **TRUE for plain
  process death**, FALSE under systemd cgroup containment. Designed on macOS
  launchd where a setsid child is NOT killed when its parent exits.

## 4. Fix map (components 1–8)

| # | Component | Where | Behavior |
|---|-----------|-------|----------|
| 1 | Executor scope escape | `daemon/tools/upgrade_journal.py` (`spawn_executor` + `build_scope_argv`) | Linux+systemd → `systemd-run --user/--scope --unit=ensemble-upgrade-<run_id>` (own cgroup). Non-Linux / no-systemd → today's `start_new_session=True` path BYTE-IDENTICAL. |
| 2 | Death-anchored journaling | `scripts/upgrade/lib.sh` (`_trap_install_signal_handlers` + `_signal_handler`) — applied in promote.sh / restart.sh / rollback.sh / stage.sh | TERM/HUP/INT traps journal a `halt` event + release the lock + exit with 128+N. Race-safe with EXIT trap (`_LOCK_RELEASED` guard). |
| 3 | TXN heartbeat + owner-liveness stale-break | `scripts/upgrade/lib.sh` (`journal_open_txn`, `journal_heartbeat`, `_txn_heartbeat_stale`, `_txn_owner_dead`, `adopt_stale_txn`) and `launcher.sh` (`_journal_sweep`) | New `in_flight.last_heartbeat` field refreshed at every `lock_heartbeat` site. Two gates now exist (deliberate design, documented): primary `SWEEP_STALE_S=600` (age), added `HEARTBEAT_STALE_S=300 × owner-dead` (liveness fast path). Closes the r-f82e gap where the executor died 2s in (no heartbeat) but the sweep waited the full 600s. |
| 4 | `_iso_to_epoch` / `_js_iso_to_epoch` GNU fix | `scripts/upgrade/lib.sh` (`:84-`) and `launcher.sh` (`:217-`) | uname dispatch mirrors `atomic_flip` (47630be0): BSD `date -ju -f …`, GNU `date -d …`. Restores the boot-sweep self-heal on Linux (incident doc §3.9 #1). |
| 5 | Reaper signal surfacing | `daemon/services/upgrade_journal_sweep.py` (`_reaper_worker` + `_journal_executor_exit`) | `os.WIFSIGNALED` / `os.WTERMSIG` + `signal.Signals(...).name` → journal detail reads "terminated by SIGTERM (15)" rather than bare 143. |
| 6 | upgrade.log timestamp hygiene | `scripts/upgrade/lib.sh` (`_log_ts`, `_log_tsl`) | Portable `date -u +'…'` wrapper (BSD/GNU identical for OUTPUT). New wrapper for opt-in timestamped log writes; existing unanchored writes preserved BYTE-IDENTICALLY. |
| 7 | `restart_via_launcher` opt-in systemctl path (stretch) | `scripts/upgrade/lib.sh` (`:1173-`) | Linux+systemd + `ENSEMBLE_RESTART_UNIT` set → `systemctl start <unit>`. Default = nohup (BYTE-IDENTICAL). DEFERRED: unit-name auto-discovery (units are hand-provisioned, out-of-repo) belongs to a separate commission per live-install doc §1.7 #4. |
| 8 | Incident summary doc | `docs/incidents/2026-09-28-promote-cgroup-teardown.md` (this file) | Faithful summary; cites the live-install doc as authoritative. |

## 5. Constraints honored

- macOS / BSD paths BYTE-IDENTICAL where not explicitly changed.
- Every Linux branch is guarded (`uname -s` dispatch, `/run/systemd/system`
  check, `ENSEMBLE_RESTART_UNIT` opt-in for comp 7).
- No live-daemon / ensemble_prod touches. No push. No merge.
- Env-safety (ambient `POSTGRES_*` scrub) verified before any DB-touching
  test (per the documented incident pattern — twice leaked probes into
  LIVE ensemble_prod on 2026-09-21 and 2026-09-26).
- All anchors grep-verified before editing; drift noted (see commit message).
- **Unit-name reconciliation:** live ops docs reference `ensemble-exec-*` (the
  provisioned unit pattern); this repo's code uses `ensemble-upgrade-*` (the
  systemd-run scoped unit prefix from comp 1). Both match the polkit regex —
  the two namespaces are intentional (comp 7 hand-provisioned; comp 1
  runtime-generated).
- **Nested-cgroup caveat (comp 7 default nohup path):** when
  `ENSEMBLE_RESTART_UNIT` is unset (the default), the nohup'd launcher
  inherits the calling executor's cgroup scope, so a unit-level stop on
  `ensemble-live.service` will NOT reach the restarted daemon. This is
  the same family as the deploy-ownership conflict (see critical notes);
  opt-in to comp 7 systemctl path for production deploys.

## 6. Tests

Component-level pin tests (one per component):

1. **Spawn guard** — three-way pin (Linux+systemd → systemd-run argv with
   run_id unit name; Linux no-systemd → fallback; non-Linux → byte-identical
   legacy path) via mocked detection (`_scope_detect_fn` test seam).
2. **Signal traps** — TERM mid-flight → halt journal event written + lock
   released + no double-release with EXIT trap.
3. **TXN heartbeat** — stale-heartbeat+dead-owner → swept; fresh-heartbeat
   or live-owner → held; mirrored tables agree.
4. **ISO parsing** — GNU + BSD arms (mirror atomic_flip test shape,
   `tests/test_atomic_flip.sh:222-294`).
5. **Reaper signal attribution** — signal name surfaces in journal detail.
6. **Timestamped log lines** — `_log_tsl` writes ISO-prefixed lines.

Existing `tests/test_release_journal.sh` + `tests/test_atomic_flip.sh`
must remain green; the BSD/GNU portable invariants must remain intact.

Note: the 6 numbered items map to comps 1–6 (the pinned components); comp 7
(`restart_via_launcher` opt-in systemctl path) is exercised via the comp7
stretch slot in `tests/test_promote_cgroup_survivorship.sh`, and comp 8 is
this doc itself.

## 7. What the daemon did right

- Clean shutdown: 0.71s drain, 134/134 jobs, no data loss, exit 143 (counted
  as success via `SuccessExitStatus=143`).
- The versioned-release layout held: `current → releases/v0.16.0` was intact,
  so rollback remained trivially available throughout.

## 8. References

- **Source of truth (READ-ONLY live install):**
  `/home/nea/agents-ensemble/docs/2026-09-28-ensemble-selfupgrade-cgroup-incident.md`
  (414 lines: executive summary, timeline, root-cause analysis, host-side
  hardening §2, VM-side validation checklist §3, known-open anomalies §3.9).
- **Branch:** `fix/promote-cgroup-survivorship` @ base `84869379` (v0.16.1).
- **Precedent — portable atomic_flip:** commit `47630be0`
  (`fix/portable-atomic-flip-linux`, 2026-09-21) — the uname dispatch shape
  comp 4 mirrors.

## 9. Deferred items (out of scope; recorded for follow-up)

- **Auto-discovery of the right unit name** for comp 7 (live-install doc §1.7 #4):
  units are hand-provisioned (out-of-repo), so the env-var opt-in is the
  safe shape until the installer-adoption commission lands.
- **Installer adoption of host-side artifacts** (live-install doc §1.7 #4):
  polkit rule + ensemble-live-watchdog — currently hand-provisioned on
  ensemble-vm; an installer drop would prevent drift on a reinstall.
- **`retention_evict` no-op on Linux** (live-install doc §3.9 #2): separate
  BSD-ism site; same shape as the ISO fix, needs the same Linux-portability
  pass.