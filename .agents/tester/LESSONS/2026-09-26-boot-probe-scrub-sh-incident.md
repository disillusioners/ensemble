# Boot-probe env-scrub failure → LIVE PG contact (2026-09-26, agent-snapshot-v1 gate)

## What happened
Gate-2 worker (snap-g2-lane) ran the mandated POSTGRES_* env scrub as an inline
`source /tmp/scrub_env.sh` line inside the bash tool. The bash tool executes
multi-line commands under `/bin/sh` (dash), which has no `source` builtin →
`/bin/sh: 5: source: not found` was appended silently → **the scrub never ran**.
`./dev.sh` then inherited ambient `POSTGRES_HOST=10.44.0.2 POSTGRES_DB=ensemble_prod`,
connected LIVE PG, and executed its configured startup policy
`discard_on_startup=backlog-clear`: **"Cleared 228 backlog message(s)" + "228 backlog
task(s)"** (log line states in-flight/paused preserved — cleared items were queued/stale
backlog, no active jobs lost). Same mechanism as the documented Phase-D incident
(24 messages + 24 tasks). Second crash-window risk: `data_dev/ensemble.json` written
during the incident PERSISTS the PG config — any later boot using that data_dir
re-connects live even with scrubbed env.

## Root cause (two layers)
1. **Executor layer**: `source` under dash = silent no-op. Any bashism in an inline
   scrub (process substitution `< <(printenv)`, `source`, arrays) fails the same way.
2. **Dispatcher layer (mine)**: my dispatch messages provided the scrub as an inline
   bash snippet. Inline snippets are shell-interpreter-dependent; nothing verified the
   scrub actually happened before boot.

## Corrective pattern (now mandatory for ANY boot/DB-adjacent run)
1. Put the scrub in a standalone file with `#!/bin/bash` shebang; invoke via
   `bash /tmp/scrub_and_boot.sh` (never `source`, never inline).
2. The wrapper must ECHO the surviving risky vars and ASSERT empty BEFORE launching:
   ```bash
   #!/bin/bash
   for v in $(printenv | grep -oE '^(POSTGRES|ENSEMBLE|DATABASE|PG_TEST)[A-Za-z_]*'); do unset "$v"; done
   unset SSL_CERT_FILE SSL_CERT_DIR 2>/dev/null
   surv=$(printenv | grep -cE '^(POSTGRES|ENSEMBLE|DATABASE|PG_TEST)' || true)
   echo "[scrub] surviving risky vars: $surv (must be 0)"
   [ "$surv" -eq 0 ] || { echo "REFUSING TO BOOT"; exit 42; }
   exec "$@"
   ```
3. Post-boot verification: daemon log must show `No PostgreSQL ENV vars detected,
   defaulting to sqlite` (or an explicitly-provisioned throwaway DSN). A log line
   `PostgreSQL ENV vars detected` = ABORT + investigate.
4. Delete any `data_dev*/` or `data/` config written during a mis-scoped boot from the
   worktree before further work (it pins the wrong DB).

## Related project knowledge
- Worker KB entry: `bash-tool-sh-vs-bash-scrub-gotcha` (experience(), 2026-09-26).
- Pre-existing SQLite boot trap (not this incident, co-observed): fresh-SQLite boot
  dies at migration 20260714_000001 (`DROP CONSTRAINT IF EXISTS`, PG-only DDL) —
  identical at base and HEAD; documented family (QUARANTINE rows 43/45/58, LESSONS
  2026-09-04). Boot gate adjudication must treat this as PRE-EXISTING-ENV.
- dev.sh itself hardcodes `export PORT=8079` (no env override) and requires
  OPENAI_API_KEY — a scrubbed worktree boot therefore uses direct
  `uvicorn daemon.api:app --port 18079` (documented deviation).
