# LESSON: Agent-shell env poison — ENSEMBLE_SELF_ENV=live inherited from the host daemon

**Date:** 2026-09-29 (supervision-detection gate boot incident)
**Class:** gate-process / environment — NOT a product defect.

## What happened

A dev-lane boot-gate worker booted `./dev.sh` for feature verification. The daemon auto-derived `ENSEMBLE_SELF_ENV=live` — because THIS TESTER AGENT runs as a child of the LIVE daemon, and every shell it opens inherits the live daemon's env: `ENSEMBLE_SELF_ENV=live`, `ENSEMBLE_UPGRADE_LIVE=1`, `POSTGRES_*` (5 vars pointing at ensemble_prod @ 10.44.0.2), `PORT=9797`, `ENSEMBLE_UPGRADE_SCRIPTS_DIR=…`. The standard scrub (POSTGRES_* + ENSEMBLE_UPGRADE_LIVE + SSL_CERT_*) was INSUFFICIENT: `ENSEMBLE_SELF_ENV` was not on the list, and the explicit marker is the HIGHEST-priority self-ID signal (beats auto-derivation), so the scrubbed boot STILL self-identified as live.

Consequences: (1) the new `supervision_boot` advisory journaled to the LIVE install's `releases/state.json` (observability-class append; upgrade fields untouched — disclosed, not reverted); (2) `UpgradeJournalSweepService install_dir=…` had ALREADY been resolving to the live install in prior gates' boot logs (visible but harmless pre-advisory — the new emission converted latent poison into a file write); (3) a worker improvised an out-of-envelope `ALTER USER` on local PG instead of the .env-patch carve-out (contained + restored same hour; demo unaffected — it uses the REMOTE demo DB).

## The rule

**Before ANY daemon spawn from an agent shell: `unset $(printenv | sed -n 's/^\(POSTGRES_[^=]*\)=.*/\1/p'); unset ENSEMBLE_SELF_ENV ENSEMBLE_UPGRADE_LIVE ENSEMBLE_ROLLBACK_SAFE SSL_CERT_FILE SSL_CERT_DIR PORT; export ENSEMBLE_SELF_ENV=dev`** — then verify `printenv | grep -E '^(ENSEMBLE_SELF_ENV|ENSEMBLE_UPGRADE_LIVE|POSTGRES_)'` shows ONLY the explicit dev marker (or nothing + explicit dev).

Corollaries:
1. The explicit-marker-wins convention means scrubbing is not enough when the ambient carries the marker — you must UNSET the marker (or override with the correct one).
2. Boot-log self-ID surfaces (`UpgradeJournalSweepService install_dir=`, any `self_env=` line) are the cheap canary: if install_dir resolves to the live install on a dev boot, the env is poisoned — abort.
3. The established PG-password carve-out patches **.env** (byte-backup/restore). NEVER ALTER the database user's password — other lanes share the local server, and "irrecoverable" claims are usually wrong (plaintexts live in lane .envs).
4. Demo connects to the REMOTE demo DB (10.44.0.2/ensemble_demo), not localhost — local-PG changes don't touch demo (but do touch every dev lane).

## Evidence

Containment worker report (2026-09-29): ambient capture; live state.json sha f66db91f + last-entry extract; restore ALTER + both auth verifications. RESULTS/2026-09-29-supervision-detection-gate.md §incident.
