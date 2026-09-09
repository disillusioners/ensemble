# 06 — Restart + Upgrade Runbook

last-verified-against: v0.12.4

Live restart/upgrade is **human-only** for maintenancer (architect
§5.2 Tier 3 + §5.4 live-rung gate). This runbook covers the
**prep + dry-run + handoff** path that maintenancer executes.

## R0 — Pause-first quiesce

**Trigger:** every restart/upgrade operation starts here.

**Steps:** see §05 Runbook R1.

## R1 — Boot DB preflight (DR-1 frozen-binary)

**Trigger:** every daemon boot, including the frozen entry
(`run_app.py` → `daemon/__main__.py:main`).

**Steps:**
- `_boot_db_preflight()` runs PostgreSQL reachability + SQLSTATE auth
  checks; replicates the lifespan's minimal `ensemble.json` read
  (`ENSEMBLE_DATA_DIR > DATA_DIR > ./data` precedence — see
  `daemon/api.py` lifespan) to decide the database backend BEFORE
  config loads. PG = `ensemble_prod`; SQLite = `data/instances.db`
  (reliquary; never prod).

**Verification:**
- Boot log shows `Creating PostgreSQL engine ensemble_prod` OR the
  SQLite fallback line.
- Exit code 0 on the preflight; auth-failure SQLSTATE
  (`28P01`/`28000`/`28P02`) halts the boot loud.

**Anchor:** `daemon/__main__.py:104` (`_boot_db_preflight`).

## R2 — Journal sweep (boot-time recovery)

**Trigger:** every daemon boot (`launcher.sh`).

**Steps:** the launcher embeds a journal sweep as the boot-time
recovery backstop. It scans `$INSTALL_DIR/releases/state.json` for
unresolved in-flight txns and either auto-recovers (if clearly
abandoned) or halts for human (loud, never silent).

**Verification:**
- Boot log shows `journal sweep: clean` OR `journal sweep: adopted N
  stale txn(s)`.
- `current` symlink resolves to the same target as the journal's
  `previous` field.

## R3 — Live gate via 3-factor

**Trigger:** any live `system_upgrade` arming (never `system_restart` —
that one refuses live unconditionally).

**Steps:**
1. Run `system_upgrade(dry_run=True)` → preflight persists ONLY the
   nonce pending-action (`run_id`, `nonce`, `kind=upgrade`, `env`,
   `target=version`, `issued_to_instance=current_instance_id`); no
   pipeline mutation. Tool output includes the nonce for the user to
   echo in-thread.
2. **Relay the nonce verbatim** to the user via `ask_user` or an
   in-thread message. **MUST NOT fabricate or echo the nonce
   agent-side** (Cardinal #4).
3. User replies in-thread within 15 min echoing the nonce (in a
   user-origin message — F2 satisfied via
   `USER_ORIGIN_SOURCES = {api, telegram:, webhook:, whatsapp:,
   discord:, slack:}` at `upgrade_journal.py:1076-1084`).
4. Call `system_upgrade(user_confirmed=True, nonce=<echoed>)` →
   3-factor gate clears (F1 + F2 + F3 all present); pipeline arms.

**Verification:**
- `upgrade_journal.py` records the pending action then the commit.
- The gate refusal path is exercised by integration tests; if any
  factor is missing the tool returns a refusal event WITHOUT
  mutating state.

**Anchor:** `daemon/tools/upgrade_tools.py:1877-2036` (gate body).

## R4 — `adopt_stale_txn`

**Trigger:** `promote.sh` refuses with `txn-busy`.

**Steps:**
1. Inspect `releases/state.json` for the unresolved in_flight row.
2. Run `adopt_stale_txn` (lib.sh:1352) — preflight handling that
   adopts the txn or refuses if it is actively being mutated by
   another process.
3. Re-run `promote.sh`.

**Verification:**
- The adopt diagnostics output shows the resolved txn + adopted
  state.
- `promote.sh` proceeds past the txn-busy refusal.

**Anchor:** `scripts/upgrade/lib.sh:1352` (`adopt_stale_txn`); caller
at `scripts/upgrade/promote.sh:151`.

## R5 — Atomic flip

**Trigger:** promote gates cleared (livez → readyz → version-verify →
soak); promote preflight calls the flip.

**Steps:**
1. `atomic_flip <ver>` builds `current.new.$$` symlink to
   `releases/<ver>`, then `mv -h -f` over `current`. The `mv -h`
   swaps the SYMLINK ITSELF (BSD); plain `mv -f` would follow the
   existing `current→releases/<old>` link and move the new link INTO
   the old release dir, silently leaving `current` pointing at the
   OLD release.
2. The flip is `rename(2)`-semantics — atomic.
3. Subsequent process invocations resolve via `$INSTALL_DIR/current`
   first (verified foundation — `launcher.sh resolve_binary`).

**Verification:**
- `readlink $INSTALL_DIR/current` returns `releases/<ver>`.
- The launcher boot picks up the new version (boot log shows the new
  version on the next start).

**Anchor:** `scripts/upgrade/lib.sh:1125-1138` (atomic_flip body).

## R6 — Cycle ledger

**Trigger:** every upgrade cycle ends.

**Steps:** `scripts/upgrade/ledger_check.py` derives the per-cycle
ledger and the live-promotion gate verdict (`--f2-verified-closed`).
The script is journal-derived (NEVER writes); the human-readable
table is DERIVED from the journal + the ledger checker.

**Verification:**
- `ledger_check.py` exits 0 when F2 has been verified closed AND
  N-clean-cycles is satisfied.
- `ledger-check: REFUSED` line is printed when current state fails
  the gate.

**Anchor:** `scripts/upgrade/ledger_check.py:277` (live-rung gate
hard-BLOCK on F2-open before count logic).

## R7 — Restart-required kill-switch activation

**Trigger:** config flips that require a restart
(`ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED`, kill-switch defaults
default ON).

**Steps:**
1. Persist the new value in `INSTALL_DIR/.env`.
2. Pause-first (§R0).
3. Restart via the launcher (the launcher re-execs the new binary
   against the new config; never a raw kill).
4. Verify the new behavior on the post-restart boot log.

**Verification:**
- Boot log shows the new kill-switch value as activated.
- The feature works as expected post-restart.

**Anchor:** Rollout pattern per
`.agents/shared/planning/kv-ambient-awareness-fix/plan-overview.md`.

## Cross-refs

- §01 architecture (DR-1 frozen-binary preflight)
- §04 traps (3-factor nonce relay; adopt_stale_txn)
- §05 runbook R1 (pause-first reused)
