# 06 — Restart + Upgrade Runbook

last-verified-against: v0.12.4

## R0 — Pause-first quiesce
**T:** every restart/upgrade starts here.
**S:** see §05 R1.

## R1 — Boot DB preflight
**T:** every daemon boot.
**S:** `_boot_db_preflight()` runs PG reachability + SQLSTATE auth; decides DB backend BEFORE config loads.
**V:** boot log shows `Creating PostgreSQL engine ensemble_prod`. **A:** `daemon/__main__.py:104`.

## R2 — Journal sweep
**T:** every daemon boot (`launcher.sh`).
**S:** launcher embeds journal sweep. Scans `$INSTALL_DIR/releases/state.json` for unresolved in-flight txns.
**V:** boot log shows `journal sweep: clean` OR `adopted N stale txn(s)`.

## R3 — Live gate via 3-factor
**T:** any live `system_upgrade` arming (never `system_restart`).
**S:** `system_upgrade(dry_run=True)` persists nonce. Relay nonce verbatim via `ask_user`; agent MUST NOT fabricate or echo. User replies in-thread within 15 min echoing nonce (F2 via `USER_ORIGIN_SOURCES` at `upgrade_journal.py:1081-1083`). `system_upgrade(user_confirmed=True, nonce=<echoed>)` clears 3-factor.
**V:** journal records pending then commit. **A:** `daemon/tools/upgrade_tools.py:1877-2036`.

## R4 — `adopt_stale_txn`
**T:** `promote.sh` refuses with `txn-busy`.
**S:** inspect `releases/state.json`; run `adopt_stale_txn` (lib.sh:1352); re-run `promote.sh`.
**V:** adopt diagnostics shows resolved txn. **A:** `scripts/upgrade/lib.sh:1352`; caller `promote.sh:151`.

## R5 — Atomic flip
**T:** promote gates cleared.
**S:** `atomic_flip <ver>` builds `current.new.$$` symlink, then `mv -h -f` over `current`. `mv -h` swaps SYMLINK (BSD).
**V:** `readlink $INSTALL_DIR/current` returns `releases/<ver>`. **A:** `scripts/upgrade/lib.sh:1125-1138`.

## R6 — Cycle ledger
**T:** every upgrade cycle ends.
**S:** `scripts/upgrade/ledger_check.py` derives per-cycle ledger + live-promotion gate verdict. Journal-derived.
**V:** exits 0 when F2 closed. **A:** `scripts/upgrade/ledger_check.py:277`.

## R7 — Restart-required kill-switch activation
**T:** config flips requiring restart.
**S:** persist new value in `INSTALL_DIR/.env`; pause-first (§R0); restart via launcher; verify on boot log.
**V:** boot log shows new kill-switch value activated. **A:** Rollout per `.agents/shared/planning/kv-ambient-awareness-fix/plan-overview.md`.
