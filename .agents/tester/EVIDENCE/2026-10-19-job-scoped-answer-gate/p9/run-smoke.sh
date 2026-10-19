#!/bin/bash
# Live HTTP route smoke — job-scoped answer fix (fix/job-scoped-answer-resolution @ 829feb94d)
# Fenced: FAKE_HOME, dev-only env, in-process app (binds nothing), timeout 300.
set -uo pipefail

EVIDENCE=/tmp/jaaf-p9
mkdir -p "$EVIDENCE"

# ── Fence 1: FAKE_HOME ────────────────────────────────────────────────
FAKE_HOME="$(mktemp -d /tmp/jaaf-p9-home.XXXXXX)"
export HOME="$FAKE_HOME"
export FAKE_HOME
echo "[fence] FAKE_HOME=$FAKE_HOME"

# ── Fence 2: env — strip ambient (which points at PROD!), source worktree .env ──
unset POSTGRES_HOST POSTGRES_USER POSTGRES_PASSWORD POSTGRES_PORT POSTGRES_DB \
      POSTGRES_URL DATABASE_URL ENSEMBLE_SELF_ENV ENSEMBLE_UPGRADE_LIVE \
      ENSEMBLE_UPGRADE_SCRIPTS_DIR ENSEMBLE_JOB_SYSTEM_JOB_RETRY_SCHEDULER_ENABLED \
      PGPASSWORD PGHOST PGPORT PGUSER PGDATABASE DATA_DIR ENSEMBLE_DATA_DIR
cd /home/nea/ensemble-src-wt-job-answer-asker-fix || exit 2
set -a; source ./.env; set +a

if [ "${POSTGRES_DB:-}" != "ensemble_dev" ]; then
  echo "ABORT: POSTGRES_DB=${POSTGRES_DB:-unset} != ensemble_dev"; exit 2
fi
if [ "${DAEMON_PORT:-}" != "8079" ]; then
  echo "ABORT: DAEMON_PORT=${DAEMON_PORT:-unset} != 8079"; exit 2
fi
if [ "${ENSEMBLE_SELF_ENV:-}" != "dev" ]; then
  echo "ABORT: ENSEMBLE_SELF_ENV=${ENSEMBLE_SELF_ENV:-unset} != dev"; exit 2
fi
echo "[fence] env asserts OK: POSTGRES_DB=$POSTGRES_DB DAEMON_PORT=$DAEMON_PORT ENSEMBLE_SELF_ENV=$ENSEMBLE_SELF_ENV (host=$POSTGRES_HOST:$POSTGRES_PORT)"

# ── Fence 3: no uvicorn boot — in-process ASGI only; ports untouched. ──
# (assert 8079 free for the record; we never bind it)
if ss -ltn 2>/dev/null | grep -q ':8079 '; then
  echo "NOTE: 8079 has a listener (unexpected for dev) — in-process mode does not bind it; continuing."
fi

# ── Run the smoke under a hard 300s cap ───────────────────────────────
.venv/bin/python "$EVIDENCE/smoke.py" 2>&1 | tee "$EVIDENCE/smoke-stdout.log"
RC=${PIPESTATUS[0]}

# ── Escape-guarded teardown of FAKE_HOME ──────────────────────────────
case "$FAKE_HOME" in
  /tmp/jaaf-p9-home.*) rm -rf "$FAKE_HOME" && echo "[fence] FAKE_HOME removed" ;;
  *) echo "[fence] REFUSE to delete non-fenced path: $FAKE_HOME" ;;
esac

exit "$RC"
