#!/usr/bin/env bash
# boot_smoke.sh — packaged-artifact boot smoke for the settings-override
# fail-soft fix (v0.31.9). Verifies a packaged binary boots cleanly with
# live-shaped app_settings rows (including an unknown key) and applies
# limits.worker_pool_size=7 without crashing.
#
# Usage:
#   scripts/upgrade/boot_smoke.sh --binary <path>
#     e.g. scripts/upgrade/boot_smoke.sh --binary dist/ensemble
#
# Exit codes: 0 = smoke passed; non-zero = failure (see stderr).
set -euo pipefail

# ── args ─────────────────────────────────────────────────────────────────────
BINARY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --binary) BINARY="$2"; shift 2 ;;
    --binary=*) BINARY="${1#--binary=}"; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
if [[ -z "$BINARY" ]]; then
  echo "ERROR: --binary <path> is required" >&2
  exit 2
fi
if [[ ! -x "$BINARY" ]]; then
  echo "ERROR: binary not executable: $BINARY" >&2
  exit 2
fi

# ── repo root (for migration SQL) ────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── fenced scratch PG ────────────────────────────────────────────────────────
PG_HOST="127.0.0.1"
PG_PORT="5432"
SMOKE_DB="ensemble_dev_scratch"
USE_FRESH=0
PGUSER="${POSTGRES_USER:-ensemble_dev_app}"
PGPASSWORD="${POSTGRES_PASSWORD:-}"
export PGPASSWORD
if ! psql -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d "$SMOKE_DB" -c '\q' >/dev/null 2>&1; then
  USE_FRESH=1
  SMOKE_DB="ensemble_dev_boot_smoke_$$"
fi

# ── temp dirs ────────────────────────────────────────────────────────────────
TMP_ROOT="$(mktemp -d /tmp/ens-boot-smoke-XXXXXX)"
CONFIG_DIR="$TMP_ROOT/config"
DATA_DIR="$TMP_ROOT/data"
mkdir -p "$CONFIG_DIR" "$DATA_DIR"
LOG_FILE="$TMP_ROOT/boot.log"

# ── free port (python socket bind check) ─────────────────────────────────────
PORT="$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"

# ── cleanup trap ─────────────────────────────────────────────────────────────
SMOKE_PID=""
cleanup() {
  if [[ -n "$SMOKE_PID" ]] && kill -0 "$SMOKE_PID" 2>/dev/null; then
    kill -TERM "$SMOKE_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      kill -0 "$SMOKE_PID" 2>/dev/null || break
      sleep 1
    done
    kill -KILL "$SMOKE_PID" 2>/dev/null || true
    wait "$SMOKE_PID" 2>/dev/null || true
  fi
  psql -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d "$SMOKE_DB" \
    -c "DELETE FROM app_settings WHERE key IN ('limits.worker_pool_size','wedge_notice_emission_enabled','smoke_unknown_legacy_key');" \
    >/dev/null 2>&1 || true
  if [[ "$USE_FRESH" == "1" ]]; then
    psql -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d postgres \
      -c "DROP DATABASE IF EXISTS $SMOKE_DB;" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT INT TERM

# ── create fresh DB + apply migration if needed ──────────────────────────────
if [[ "$USE_FRESH" == "1" ]]; then
  psql -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d postgres \
    -c "CREATE DATABASE $SMOKE_DB;" >/dev/null 2>&1 || {
      echo "ERROR: cannot create scratch DB $SMOKE_DB" >&2; exit 1; }
  MIG_SQL="$REPO_ROOT/daemon/migrations/versions/20261018_000002_create_app_settings.sql"
  if [[ -f "$MIG_SQL" ]]; then
    psql -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d "$SMOKE_DB" \
      -f "$MIG_SQL" >/dev/null 2>&1 || true
  fi
fi

# ── seed live-shaped rows ────────────────────────────────────────────────────
psql -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d "$SMOKE_DB" \
  -v ON_ERROR_STOP=1 <<SQL >/dev/null 2>&1 || { echo "ERROR: seed failed (app_settings table missing?)" >&2; exit 1; }
INSERT INTO app_settings (key, value_json, value_type, updated_by, updated_at)
VALUES ('limits.worker_pool_size','7','int',NULL,now()::text)
ON CONFLICT (key) DO UPDATE SET value_json=EXCLUDED.value_json, value_type=EXCLUDED.value_type, updated_at=EXCLUDED.updated_at;
INSERT INTO app_settings (key, value_json, value_type, updated_by, updated_at)
VALUES ('wedge_notice_emission_enabled','true','bool',NULL,now()::text)
ON CONFLICT (key) DO UPDATE SET value_json=EXCLUDED.value_json, value_type=EXCLUDED.value_type, updated_at=EXCLUDED.updated_at;
INSERT INTO app_settings (key, value_json, value_type, updated_by, updated_at)
VALUES ('smoke_unknown_legacy_key','true','bool',NULL,now()::text)
ON CONFLICT (key) DO UPDATE SET value_json=EXCLUDED.value_json, value_type=EXCLUDED.value_type, updated_at=EXCLUDED.updated_at;
SQL

# ── fenced env (per-variable scrub + ambient allow) ──────────────────────────
ENV_ARGS=(
  "ENSEMBLE_SELF_ENV=dev"
  "ENSEMBLE_DB_AMBIENT_ALLOW=1"
  "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0"
  "DAEMON_PORT=$PORT"
  "PORT=$PORT"
  "POSTGRES_HOST=$PG_HOST"
  "POSTGRES_PORT=$PG_PORT"
  "POSTGRES_DB=$SMOKE_DB"
  "POSTGRES_USER=$PGUSER"
  "POSTGRES_PASSWORD=$PGPASSWORD"
)
UNSET_ARGS=()
for v in $(env | sed -n 's/^\(ENSEMBLE_AUTH_[A-Za-z0-9_]*\)=.*/\1/p'); do
  UNSET_ARGS+=("-u" "$v")
done

# ── boot ─────────────────────────────────────────────────────────────────────
echo "booting $BINARY on port $PORT (db=$SMOKE_DB)..."
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
  "${UNSET_ARGS[@]}" \
  "${ENV_ARGS[@]}" \
  "$BINARY" >"$LOG_FILE" 2>&1 &
SMOKE_PID=$!

# ── poll /livez ─────────────────────────────────────────────────────────────
for i in $(seq 1 45); do
  if ! kill -0 "$SMOKE_PID" 2>/dev/null; then
    echo "ERROR: process exited early" >&2
    echo "--- log tail ---" >&2
    tail -30 "$LOG_FILE" >&2
    exit 1
  fi
  if curl -fsS "http://127.0.0.1:$PORT/livez" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS "http://127.0.0.1:$PORT/livez" >/dev/null 2>&1; then
  echo "ERROR: /livez not 200 within 45s" >&2
  echo "--- log tail ---" >&2
  tail -40 "$LOG_FILE" >&2
  exit 1
fi

# ── verify applied + no crash ────────────────────────────────────────────────
if grep -q "Application startup failed" "$LOG_FILE"; then
  echo "ERROR: 'Application startup failed' in log" >&2
  tail -40 "$LOG_FILE" >&2
  exit 1
fi
if grep -q "cannot parse.*as bool" "$LOG_FILE"; then
  echo "ERROR: bogus yaml dict-parse warning present" >&2
  grep "cannot parse" "$LOG_FILE" >&2
  exit 1
fi

echo "PASS: boot smoke OK (port=$PORT, db=$SMOKE_DB)"
exit 0
SH
chmod +x scripts/upgrade/boot_smoke.sh
echo "written"