# 2026-09-30 — Boot-smoke env-poison RECURRENCE: `unset ENSEMBLSELF_ENV` is INSUFFICIENT on multi-install hosts; explicit marker REQUIRED

**Incident class:** recurrence of `2026-09-29-agent-shell-env-poison-ENSEMBLE_SELF_ENV.md` (supervision-detection gate).
This is the 3rd event in the family and it FALSIFIES the 2026-09-29 remediation for the boot lane.

## What happened (v0.16.6 gate, P3 boot smoke, worker 5b2686e3)

Scrubbed-env boot wrapper (standalone `#!/bin/bash`, unset `ENSEMBLE_SELF_ENV ENSEMBLE_UPGRADE_LIVE
ENSEMBLE_UPGRADE_SCRIPTS_DIR INVOCATION_ID PORT SSL_CERT_* POSTGRES_*`, echo-verify zero survivors)
STILL resolved `self_env="live"` → `install_dir=/home/nea/agents-ensemble` → appended ONE
`supervision_boot` advisory entry to the LIVE install's `releases/state.json` at 2026-09-30T00:30:46Z.

## Root cause — auto-derive reads the FILESYSTEM, not the process env

`daemon/tools/upgrade_tools.py:_auto_derive_env()` reads `~/agents-ensemble/.env` DIRECTLY
(sibling install) and finds `POSTGRES_DB=ensemble_prod` → derives `live`. A scrubbed PROCESS env
cannot prevent this: the derivation input is outside the process.

## The fix that works (proven same drill)

Explicit marker WINS over auto-derive (per `_self_env_marker` contract). Boot wrappers on this host
MUST **export `ENSEMBLE_SELF_ENV=dev`** (not merely unset it) after the scrub:

```bash
#!/bin/bash
unset ENSEMBLE_SELF_ENV ENSEMBLE_UPGRADE_LIVE ENSEMBLE_UPGRADE_SCRIPTS_DIR \
      INVOCATION_ID PORT SSL_CERT_FILE SSL_CERT_DIR
unset $(env | grep -o '^POSTGRES_[A-Z_]*'); unset $(env | grep -o '^PG[A-Z_]*')
export ENSEMBLE_SELF_ENV=dev        # <-- THE load-bearing line
export OPENAI_API_KEY=sk-dummy-boot-smoke
exec bash ./dev.sh
```

Hardened template preserved at `/tmp/p3-v166-boot/scrub_and_boot.sh` (copy into the next gate's
scratch; /tmp is volatile).

## Damage scope + disposition (3rd event)

- ONE history append (`supervision_boot` advisory) to `~/agents-ensemble/releases/state.json`;
  `current/previous/in_flight/pending_op/quarantined` ALL unchanged. Left in place + disclosed
  (pruning = a second write; worse than the disclosure). LIVE daemon in-memory state unaffected.
- Prior events: 2026-09-29 14:54:42Z (supervision gate boot) — same shape, same disposition.

## Standing rule for ALL future gate/worker boot wrappers on this host

1. Scrub (as before) — still mandatory.
2. **Export `ENSEMBLE_SELF_ENV=dev` explicitly** — unset alone is cosmetic on multi-install hosts.
3. Post-boot check: `grep -i "install_dir" boot.log` must show `<none — dev/unresolved>` or a
   /tmp sandbox — if it shows `~/agents-ensemble`, kill YOUR boot only, harden, retry once, disclose.
4. Never signal the real 9797/7979 daemons while doing any of this.

## Companion finding — local PG auth drift recurrence (2nd in 2 days)

`ALTER USER ensemble PASSWORD 'testpw'` carve-out (applied 2026-09-29 supervision gate) had DRIFTED
again by 2026-09-30 00:30 — first boot attempt failed `password authentication failed for user
"ensemble"`. Re-applied identically (dev-local PG only, sudo -u postgres, reversible; NO pg_hba
edits, NO .env edits). Owner should find what keeps resetting the local `ensemble` role password
(suspect: local PG re-init or role-restore tooling between gates).

## References

- v0.16.6 gate RESULTS: `RESULTS/2026-09-29-v0166-fix-commission-gate.md` (P3 section)
- Prior incident: `LESSONS/2026-09-29-agent-shell-env-poison-ENSEMBLE_SELF_ENV.md`
- Damage evidence: `/tmp/p3-v166-boot/boot-POISONED-attempt.log` (volatile, copied facts above)
