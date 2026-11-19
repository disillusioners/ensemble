#!/usr/bin/env bash
# boot_smoke.sh — packaged-artifact boot smoke for the settings-override
# fail-soft fix (v0.31.9). Proves a packaged artifact boots and serves
# /livez + /readyz against a fenced scratch PG, applying live-shaped
# app_settings rows (including an unknown key), WITHOUT touching the live
# daemon (9797), the live DB, or the live install dir.
#
# Usage:
#   scripts/upgrade/boot_smoke.sh --binary <path> [--config <path>]
#                                 [--agents-dir <path>] [--timeout <secs>]
#                                 [--allow-shared-cgroup]
#     e.g. scripts/upgrade/boot_smoke.sh --binary dist/ensemble
#          scripts/upgrade/boot_smoke.sh --binary releases/v0.31.9/<binary>
#
# ============================================================================
# BOOT-FENCE CONTRACT (house pattern — skill `staged-binary-boot-smoke`)
# ============================================================================
#  1. CGROUP ISOLATION — the smoke boots inside its OWN `systemd-run --user`
#     TRANSIENT unit (a NEW unit; the live ensemble-main unit is NEVER
#     touched/stopped/reloaded). There is NO shared-cgroup fallback: if
#     `systemd-run --user` is unavailable the script REFUSES unless
#     `--allow-shared-cgroup` is passed, which requires the operator to
#     launch it from a NON-daemon-child shell (an operator terminal) —
#     an agent shell inside ensemble-main.service's cgroup can be
#     SIGTERM-ed by the boot-epoch sweep (2026-10-21 incident: 5 live
#     tool-children killed).
#  2. BOTH KILL-SWITCHES PINNED + ECHOED pre-exec as [fence] lines:
#     ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 (protects live aux children)
#     ENSEMBLE_ORPHAN_F1_ENABLED=0 (the smoke runs against a SHARED scratch
#     DB — F1 orphan re-dispatch could act on other lanes' rows).
#  3. DSN FAMILY SCRUBBED + ASSERTED ABSENT pre-exec: POSTGRES_URL,
#     DATABASE_URL, PGHOST, PGUSER, PGPASSWORD, PGPORT, PGDATABASE (plus
#     the 5 POSTGRES_* and SSL_CERT_FILE/SSL_CERT_DIR). An ambient
#     POSTGRES_URL silently repoints the CHECKPOINTER pool at another DB
#     while the engine composes from POSTGRES_*.
#  4. PORT from the house range 19100-19999, proven free pre-bind (ss -ltn).
#     BOTH PORT and DAEMON_PORT are exported (a PORT-only override falls
#     back to the forbidden 8088).
#  5. FAKE_HOME (mktemp -d) + prefix-guarded rm — never a HOME-derived
#     install dir is resolved or deleted.
#  6. CONFIG PINNED: --config <path> (default: the artifact's sibling
#     config.yaml) is COPIED into the temp CONFIG_DIR and the daemon boots
#     with cwd=CONFIG_DIR so ./config.yaml is the pinned copy;
#     agents/ resolves from the ARTIFACT (never the repo tree).
#  7. PYINSTALLER ONEFILE = 2 PIDs: the boot is setsid'd (own process
#     GROUP); cleanup kills the whole GROUP, then ASSERTS the port is
#     released and escalates to SIGKILL if still held — a leaked daemon
#     must never survive holding a connection to the shared scratch DB.
#  8. /livez AND /readyz polled (readyz components database +
#     checkpoint_saver must be true) — a broken checkpoint pool must FAIL
#     the gate, not pass green.
#
# Exit codes: 0 = smoke passed; non-zero = failure (see stderr).
set -euo pipefail

# ── args ─────────────────────────────────────────────────────────────────────
BINARY=""
CONFIG_SRC=""
AGENTS_DIR=""
POLL_TIMEOUT=45
ALLOW_SHARED_CGROUP=0
PG_USER_ARG=""
PG_PASS_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --binary) BINARY="$2"; shift 2 ;;
    --binary=*) BINARY="${1#--binary=}"; shift ;;
    --config) CONFIG_SRC="$2"; shift 2 ;;
    --config=*) CONFIG_SRC="${1#--config=}"; shift ;;
    --agents-dir) AGENTS_DIR="$2"; shift 2 ;;
    --agents-dir=*) AGENTS_DIR="${1#--agents-dir=}"; shift ;;
    --timeout) POLL_TIMEOUT="$2"; shift 2 ;;
    --timeout=*) POLL_TIMEOUT="${1#--timeout=}"; shift ;;
    --allow-shared-cgroup) ALLOW_SHARED_CGROUP=1; shift ;;
    --pg-user) PG_USER_ARG="$2"; shift 2 ;;
    --pg-user=*) PG_USER_ARG="${1#--pg-user=}"; shift ;;
    --pg-password) PG_PASS_ARG="$2"; shift 2 ;;
    --pg-password=*) PG_PASS_ARG="${1#--pg-password=}"; shift ;;
    -h|--help) sed -n '2,55p' "$0"; exit 0 ;;
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ARTIFACT_DIR="$(cd "$(dirname "$BINARY")" && pwd)"
BINARY_ABS="$ARTIFACT_DIR/$(basename "$BINARY")"

# ── prefix-guarded rm (NEVER a HOME-derived or live path) ────────────────────
safe_rm() {
  local p="$1"
  case "$p" in
    /tmp/ens-boot-smoke-*|/tmp/ens-boot-smoke-home-*) rm -rf "$p" ;;
    "") : ;;
    *) echo "REFUSE: refusing to remove $p (not a /tmp/ens-boot-smoke-* path)" >&2 ;;
  esac
}

# ── temp dirs + trap installed IMMEDIATELY after mktemp ─────────────────────
TMP_ROOT="$(mktemp -d /tmp/ens-boot-smoke-XXXXXX)"
FAKE_HOME="$(mktemp -d /tmp/ens-boot-smoke-home-XXXXXX)"
SMOKE_PID=""
BOOT_PGID=""
SMOKE_EXIT=0
UNIT=""
USE_SYSTEMD=0
TORN_DOWN=0
CLEANUP_FAILED=0
cleanup() {
  local rc=$?
  if [[ "$TORN_DOWN" -eq 0 ]]; then
    teardown_smoke || true
  fi
  # restore pre-existing rows for the 3 seeded keys (delete-then-restore).
  # Credentialed (psql_pw) - bare psql has no .pgpass on this host and
  # would silently leak the 3 seeded rows into the shared scratch DB.
  CLEANUP_FAILED=0
  if [[ -n "${SMOKE_DB:-}" ]]; then
    if ! psql_pw -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d "$SMOKE_DB" \
         -c "DELETE FROM app_settings WHERE key IN ('limits.worker_pool_size','wedge_notice_emission_enabled','smoke_unknown_legacy_key');" \
         >/dev/null 2>&1; then
      echo "WARN: cleanup DELETE failed - seeded rows may remain in $SMOKE_DB" >&2
      CLEANUP_FAILED=1
    fi
    if [[ -s "${BACKUP_SQL:-}" ]]; then
      if ! psql_pw -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d "$SMOKE_DB" \
           -f "$BACKUP_SQL" >/dev/null 2>&1; then
        echo "WARN: cleanup RESTORE failed - pre-existing rows may be missing" >&2
        CLEANUP_FAILED=1
      fi
    fi
    if [[ "${USE_FRESH:-0}" == "1" ]]; then
      psql_pw -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d postgres \
        -c "DROP DATABASE IF EXISTS $SMOKE_DB;" >/dev/null 2>&1 || {
          echo "WARN: cleanup DROP DATABASE failed" >&2; CLEANUP_FAILED=1; }
    fi
  fi
  if [[ "$CLEANUP_FAILED" == "1" ]]; then
    echo "ERROR: cleanup incomplete - inspect $SMOKE_DB manually" >&2
  fi
  safe_rm "$TMP_ROOT"
  safe_rm "$FAKE_HOME"
  exit $rc
}
trap cleanup EXIT INT TERM

CONFIG_DIR="$TMP_ROOT/config"
DATA_DIR="$TMP_ROOT/data"
mkdir -p "$CONFIG_DIR" "$DATA_DIR"
LOG_FILE="$TMP_ROOT/boot.log"
BACKUP_SQL="$TMP_ROOT/backup.sql"
BOOT_SH="$TMP_ROOT/boot.sh"

# ── port helpers (house range 19100-19999) ──────────────────────────────────
port_free() {
  local p="$1"
  if command -v ss >/dev/null 2>&1; then
    ! ss -ltnH "sport = :$p" 2>/dev/null | grep -q .
  else
    python3 - "$p" <<'PY'
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
PY
  fi
}
port_released() { port_free "$PORT"; }

PORT=""
for _ in $(seq 1 80); do
  cand=$((19100 + (RANDOM % 900)))
  if port_free "$cand"; then PORT="$cand"; break; fi
done
if [[ -z "$PORT" ]]; then
  echo "ERROR: no free port in the house range 19100-19999" >&2
  exit 1
fi

# ── config pin (artifact sibling by default) + agents from the ARTIFACT ─────
if [[ -z "$CONFIG_SRC" ]]; then
  if [[ -f "$ARTIFACT_DIR/config.yaml" ]]; then
    CONFIG_SRC="$ARTIFACT_DIR/config.yaml"
  elif [[ -f "$REPO_ROOT/config.yaml" ]]; then
    CONFIG_SRC="$REPO_ROOT/config.yaml"
    echo "[fence] NOTE: artifact has no sibling config.yaml — using the repo copy" >&2
  fi
fi
if [[ -z "$CONFIG_SRC" || ! -f "$CONFIG_SRC" ]]; then
  echo "ERROR: no config.yaml to pin (looked at $ARTIFACT_DIR/config.yaml)." >&2
  echo "       Pass --config <path> explicitly." >&2
  exit 1
fi
cp "$CONFIG_SRC" "$CONFIG_DIR/config.yaml"

if [[ -z "$AGENTS_DIR" ]]; then
  if [[ -d "$ARTIFACT_DIR/agents" ]]; then
    AGENTS_DIR="$ARTIFACT_DIR/agents"
  fi
fi
if [[ -z "$AGENTS_DIR" || ! -d "$AGENTS_DIR" ]]; then
  echo "ERROR: no agents/ tree from the ARTIFACT (looked at $ARTIFACT_DIR/agents)." >&2
  echo "       Boot a staged release layout, or pass --agents-dir <path>." >&2
  exit 1
fi
ln -sfn "$AGENTS_DIR" "$CONFIG_DIR/agents"

# ── fenced scratch PG ────────────────────────────────────────────────────────
PG_HOST="127.0.0.1"
PG_PORT="5432"
SMOKE_DB="ensemble_dev_scratch"
USE_FRESH=0
PGUSER="${PG_USER_ARG:-${POSTGRES_USER:-ensemble_dev_app}}"
PGPASS="${PG_PASS_ARG:-${POSTGRES_PASSWORD:-}}"
psql_pw() { PGPASSWORD="$PGPASS" psql "$@"; }
LOCKFILE="/tmp/ens-boot-smoke-scratch.lock"
exec 9>"$LOCKFILE"
echo "acquiring shared-scratch lock ($LOCKFILE) ..."
flock -x 9
echo "lock acquired."

if ! psql_pw -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d "$SMOKE_DB" -c '\q' >/dev/null 2>&1; then
  USE_FRESH=1
  SMOKE_DB="ensemble_dev_boot_smoke_$$"
fi

# ── auth preflight: distinguish auth vs DB-absent vs no-CREATEDB ────────────
AUTH_PROBE="$(psql_pw -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d "$SMOKE_DB" -tAc "SELECT 1;" 2>&1)" || true
if [[ "$AUTH_PROBE" != "1" ]]; then
  if echo "$AUTH_PROBE" | grep -qi "password authentication failed\|fe_sendauth"; then
    echo "ERROR: AUTH FAILURE - password authentication failed for user '$PGUSER'." >&2
    echo "       probe: ${AUTH_PROBE:-<empty>}" >&2
    exit 2
  elif echo "$AUTH_PROBE" | grep -qi "does not exist"; then
    echo "ERROR: DB ABSENT - database '$SMOKE_DB' does not exist." >&2
    echo "       probe: ${AUTH_PROBE:-<empty>}" >&2
    exit 3
  fi
fi

# ── fresh-DB privilege preflight (fail FAST with a readable message) ────────
if [[ "$USE_FRESH" == "1" ]]; then
  PROBE_ERR="$(psql_pw -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d postgres -tAc \
    "SELECT rolcreatedb FROM pg_roles WHERE rolname=current_user;" 2>&1)" || true
  CREATEDB="$(printf '%s' "$PROBE_ERR" | tr -d '[:space:]')"
  if [[ "$CREATEDB" != "t" ]]; then
    echo "ERROR: the fresh-DB fallback needs a CREATEDB role." >&2
    echo "       probe output: ${PROBE_ERR:-<empty>}" >&2
    echo "       role '$PGUSER' has rolcreatedb='${CREATEDB:-<unknown>}' (the pre-existing" >&2
    echo "       ensemble_dev_app role has createdb=FALSE)." >&2
    echo "       Use the existing ensemble_dev_scratch path instead (it exists and" >&2
    echo "       authenticates) — i.e. make that DB reachable and re-run." >&2
    exit 1
  fi
  psql_pw -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d postgres \
    -c "CREATE DATABASE $SMOKE_DB;" >/dev/null 2>&1 || {
      echo "ERROR: cannot create scratch DB $SMOKE_DB" >&2; exit 1; }
  psql_pw -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d "$SMOKE_DB" \
    -v ON_ERROR_STOP=1 <<SQL >/dev/null 2>&1 || true
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT NOT NULL PRIMARY KEY,
    value_json TEXT NOT NULL,
    value_type TEXT NOT NULL,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);
SQL
fi

# ── snapshot pre-existing rows for the 3 seeded keys (NULL-safe restore) ────
psql_pw -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d "$SMOKE_DB" -tA <<SQL >"$BACKUP_SQL" 2>/dev/null || true
SELECT format('INSERT INTO app_settings (key,value_json,value_type,updated_by,updated_at) VALUES (%L,%L,%L,%s,%L) ON CONFLICT (key) DO NOTHING;',
  key, value_json, value_type, quote_nullable(updated_by), updated_at)
FROM app_settings WHERE key IN ('limits.worker_pool_size','wedge_notice_emission_enabled','smoke_unknown_legacy_key');
SQL
sed -i '/^$/d' "$BACKUP_SQL" 2>/dev/null || true

# ── ensure app_settings exists on BOTH paths (idempotent; the migration's
#     -- DOWN section is NEVER executed here) ────────────────────────────────
psql_pw -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d "$SMOKE_DB" \
  -v ON_ERROR_STOP=1 <<SQL >/dev/null 2>&1 || { echo "ERROR: cannot ensure app_settings table" >&2; exit 1; }
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT NOT NULL PRIMARY KEY,
    value_json TEXT NOT NULL,
    value_type TEXT NOT NULL,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);
SQL

# ── seed live-shaped rows ────────────────────────────────────────────────────
psql_pw -h "$PG_HOST" -p "$PG_PORT" -U "$PGUSER" -d "$SMOKE_DB" \
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

# ── the boot wrapper (env preamble runs INSIDE the unit) ────────────────────
# NOTE: a `systemd-run --user` transient unit inherits the USER-MANAGER
# environment, NOT the launching shell's — so every override below lives in
# the wrapper, not in the launching shell.
cat > "$BOOT_SH" <<EOF
#!/bin/bash
set -euo pipefail
exec >>"$LOG_FILE" 2>&1

# Phase A — scrub the inherited environment (PER-VARIABLE; bash does not
# glob env names, so "env -u POSTGRES_*" is a silent NO-OP).
unset POSTGRES_HOST POSTGRES_USER POSTGRES_PASSWORD POSTGRES_PORT POSTGRES_DB \\
      POSTGRES_URL DATABASE_URL PGHOST PGUSER PGPASSWORD PGPORT PGDATABASE \\
      SSL_CERT_FILE SSL_CERT_DIR

# Phase B — THIS run's overrides (applied AFTER the scrub).
export POSTGRES_HOST="$PG_HOST"
export POSTGRES_PORT="$PG_PORT"
export POSTGRES_DB="$SMOKE_DB"
export POSTGRES_USER="$PGUSER"
export POSTGRES_PASSWORD="$PGPASS"
export PORT="$PORT"
export DAEMON_PORT="$PORT"
export HOME="$FAKE_HOME"
export ENSEMBLE_CONFIG="$CONFIG_DIR/config.yaml"
export ENSEMBLE_DATA_DIR="$DATA_DIR"
export ENSEMBLE_SELF_ENV=dev
export ENSEMBLE_DB_AMBIENT_ALLOW=1
export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0
export ENSEMBLE_ORPHAN_F1_ENABLED=0
export MCP_DISABLE_BUILT_IN_WEBFETCH=true

# Phase C — hard assertions (fail closed BEFORE exec).
for v in POSTGRES_URL DATABASE_URL PGHOST PGUSER PGPASSWORD PGPORT PGDATABASE; do
  if [ -n "\${!v+x}" ]; then
    echo "FATAL: DSN var \$v must be ABSENT in the boot env" >&2
    exit 78
  fi
done
if [ "\${ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES}" != "0" ] || [ "\${ENSEMBLE_ORPHAN_F1_ENABLED}" != "0" ]; then
  echo "FATAL: both kill-switches must be pinned to 0" >&2
  exit 78
fi
if [ "\${PORT}" != "\${DAEMON_PORT}" ]; then
  echo "FATAL: PORT and DAEMON_PORT must match" >&2
  exit 78
fi

# Phase D — echo the fence (the boot.log head is the only place it shows).
echo "[fence] POSTGRES_HOST=\$POSTGRES_HOST POSTGRES_PORT=\$POSTGRES_PORT POSTGRES_DB=\$POSTGRES_DB POSTGRES_USER=\$POSTGRES_USER"
echo "[fence] POSTGRES_URL=\${POSTGRES_URL:-<absent>} DATABASE_URL=\${DATABASE_URL:-<absent>} PGHOST=\${PGHOST:-<absent>} PGUSER=\${PGUSER:-<absent>} PGPASSWORD=\${PGPASSWORD:+<set>} PGPORT=\${PGPORT:-<absent>} PGDATABASE=\${PGDATABASE:-<absent>}"
echo "[fence] PORT=\$PORT DAEMON_PORT=\$DAEMON_PORT ENSEMBLE_SELF_ENV=\$ENSEMBLE_SELF_ENV ENSEMBLE_DB_AMBIENT_ALLOW=\$ENSEMBLE_DB_AMBIENT_ALLOW"
echo "[fence] ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=\$ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES ENSEMBLE_ORPHAN_F1_ENABLED=\$ENSEMBLE_ORPHAN_F1_ENABLED"
echo "[fence] HOME=\$HOME ENSEMBLE_CONFIG=\$ENSEMBLE_CONFIG ENSEMBLE_DATA_DIR=\$ENSEMBLE_DATA_DIR"
echo "[fence] dsn_family_absent=POSTGRES_URL,DATABASE_URL,PGHOST,PGUSER,PGPASSWORD,PGPORT,PGDATABASE"

cd "$CONFIG_DIR"
exec "$BINARY_ABS"
EOF
chmod 700 "$BOOT_SH"  # holds POSTGRES_PASSWORD - owner-only, never world-readable

# ── teardown (process GROUP kill + port-release assertion) ─────────────────
teardown_smoke() {
  if [[ "$TORN_DOWN" -eq 1 ]]; then return 0; fi
  TORN_DOWN=1
  # The exit status is read BEFORE the stop: stopping the unit overwrites
  # ExecMainStatus with the signal-derived code and would mask an exit 3.
  if [[ "$USE_SYSTEMD" == "1" && -n "$UNIT" ]]; then
    SMOKE_EXIT="$(systemctl --user show -p ExecMainStatus --value "$UNIT" 2>/dev/null || echo 0)"
    [[ -z "$SMOKE_EXIT" ]] && SMOKE_EXIT=0
  fi
  if [[ "$USE_SYSTEMD" == "1" && -n "$UNIT" ]]; then
    systemctl --user stop "$UNIT" >/dev/null 2>&1 || true
  fi
  if [[ -n "$BOOT_PGID" ]]; then
    kill -TERM -- "-$BOOT_PGID" 2>/dev/null || true
  fi
  if [[ -n "$SMOKE_PID" ]]; then
    kill -TERM "$SMOKE_PID" 2>/dev/null || true
  fi
  for _ in $(seq 1 15); do
    port_released && break
    sleep 1
  done
  if ! port_released; then
    echo "WARN: port $PORT still held after SIGTERM — escalating to SIGKILL" >&2
    if [[ -n "$BOOT_PGID" ]]; then
      kill -KILL -- "-$BOOT_PGID" 2>/dev/null || true
    fi
    if [[ -n "$SMOKE_PID" ]]; then
      kill -KILL "$SMOKE_PID" 2>/dev/null || true
    fi
    for _ in $(seq 1 15); do
      port_released && break
      sleep 1
    done
  fi
  if [[ "$USE_SYSTEMD" -eq 0 && -n "$SMOKE_PID" ]]; then
    if wait "$SMOKE_PID" 2>/dev/null; then SMOKE_EXIT=0; else SMOKE_EXIT=$?; fi
  fi
  return 0
}

# ── launch: transient systemd unit (preferred) or an explicit waiver ───────
child_alive() {
  if [[ "$USE_SYSTEMD" == "1" ]]; then
    # "activating" is still ALIVE - only a non-active state means the unit
    # finished (and, with --collect, was reaped).
    local st
    st="$(systemctl --user is-active "$UNIT" 2>/dev/null || true)"
    [[ "$st" == "active" || "$st" == "activating" ]]
  else
    [[ -n "$SMOKE_PID" ]] && kill -0 "$SMOKE_PID" 2>/dev/null
  fi
}

XDG_USER_RUNTIME="/run/user/$(id -u)"
if command -v systemd-run >/dev/null 2>&1 && [[ -S "$XDG_USER_RUNTIME/bus" ]]; then
  USE_SYSTEMD=1
  UNIT="ensemble-boot-smoke-$(printf '%06x' "$((RANDOM % 1679616))")"
  echo "booting $BINARY_ABS in transient unit $UNIT on port $PORT (db=$SMOKE_DB)..."
  XDG_RUNTIME_DIR="$XDG_USER_RUNTIME" systemd-run --user --collect --unit="$UNIT" \
    -p WorkingDirectory="$CONFIG_DIR" /bin/bash "$BOOT_SH" >"$TMP_ROOT/systemd-run.log" 2>&1 || true
  # the unit's MainPID (the port owner); empty while the unit is starting
  SMOKE_PID="$(systemctl --user show -p MainPID --value "$UNIT" 2>/dev/null || echo 0)"
  [[ "$SMOKE_PID" == "0" ]] && SMOKE_PID=""
  # cgroup isolation proof
  for _ in $(seq 1 10); do
    if [[ -n "$SMOKE_PID" && -r "/proc/$SMOKE_PID/cgroup" ]]; then break; fi
    sleep 1
    SMOKE_PID="$(systemctl --user show -p MainPID --value "$UNIT" 2>/dev/null || echo 0)"
    [[ "$SMOKE_PID" == "0" ]] && SMOKE_PID=""
  done
  if [[ -n "$SMOKE_PID" && -r "/proc/$SMOKE_PID/cgroup" ]]; then
    CGROUP_LINE="$(cat "/proc/$SMOKE_PID/cgroup")"
    echo "[fence] child cgroup: $CGROUP_LINE"
    if [[ "$CGROUP_LINE" != *"/user.slice/"* ]] || [[ "$CGROUP_LINE" == *"ensemble-main.service"* ]]; then
      echo "ERROR: the smoke unit is NOT cgroup-isolated from the live daemon." >&2
      echo "       $CGROUP_LINE" >&2
      echo "--- systemd-run output ---" >&2
      cat "$TMP_ROOT/systemd-run.log" >&2 2>/dev/null || true
      exit 1
    fi
  else
    echo "ERROR: could not read the child's cgroup - isolation unproven." >&2
    echo "--- systemd-run output ---" >&2
    cat "$TMP_ROOT/systemd-run.log" >&2 2>/dev/null || true
    exit 1
  fi
elif [[ "$ALLOW_SHARED_CGROUP" == "1" ]]; then
  echo "############################################################" >&2
  echo "#  LOUD WAIVER — SHARED-CGROUP BOOT                       #" >&2
  echo "#  systemd-run --user is UNAVAILABLE on this host, so the #" >&2
  echo "#  smoke boots as a plain background process and SHARES   #" >&2
  echo "#  the launching shell's cgroup. The live daemon's boot-  #" >&2
  echo "#  epoch sweep can SIGTERM same-cgroup processes.          #" >&2
  echo "#  THE SMOKE MUST BE LAUNCHED FROM A NON-DAEMON-CHILD     #" >&2
  echo "#  SHELL (an operator terminal), NOT from an agent shell. #" >&2
  echo "#  Both kill-switches remain pinned (defense-in-depth).   #" >&2
  echo "############################################################" >&2
  echo "booting $BINARY_ABS (shared cgroup, WAIVERED) on port $PORT (db=$SMOKE_DB)..."
  setsid /bin/bash "$BOOT_SH" >/dev/null 2>&1 &
  SMOKE_PID=$!
  # setsid may fork (the caller is already a process-group leader) — poll for
  # the child's real PGID instead of racing the fork.
  BOOT_PGID=""
  for _ in $(seq 1 5); do
    BOOT_PGID="$(ps -o pgid= -p "$SMOKE_PID" 2>/dev/null | tr -d ' ' || true)"
    [[ -n "$BOOT_PGID" ]] && break
    sleep 1
  done
else
  echo "ERROR: systemd-run --user is UNAVAILABLE and no --allow-shared-cgroup waiver was given." >&2
  echo "       There is NO safe shared-cgroup fallback (the boot-epoch sweep can" >&2
  echo "       SIGTERM same-cgroup processes). Run the smoke from an operator" >&2
  echo "       terminal, or on a host with a usable user systemd bus." >&2
  exit 1
fi

# ── poll /livez + /readyz ───────────────────────────────────────────────────
LIVE_CODE="000"
READY_CODE="000"
for i in $(seq 1 "$POLL_TIMEOUT"); do
  if ! child_alive; then
    echo "ERROR: the smoke process exited early" >&2
    echo "--- log tail ---" >&2
    tail -30 "$LOG_FILE" >&2
    exit 1
  fi
  LIVE_CODE="$(curl -s -o "$TMP_ROOT/livez.body" -w '%{http_code}' \
    "http://127.0.0.1:$PORT/livez" 2>/dev/null || echo 000)"
  READY_CODE="$(curl -s -o "$TMP_ROOT/readyz.body" -w '%{http_code}' \
    "http://127.0.0.1:$PORT/readyz" 2>/dev/null || echo 000)"
  if [[ "$LIVE_CODE" == "200" && "$READY_CODE" == "200" ]]; then
    break
  fi
  sleep 1
done

FAIL=0
if [[ "$LIVE_CODE" != "200" ]]; then
  echo "ERROR: /livez not 200 (got $LIVE_CODE) within ${POLL_TIMEOUT}s" >&2
  FAIL=1
fi
if [[ "$READY_CODE" != "200" ]]; then
  echo "ERROR: /readyz not 200 (got $READY_CODE) within ${POLL_TIMEOUT}s" >&2
  FAIL=1
fi

# readyz components (database + checkpoint_saver must be true)
READY_VERDICT="$(python3 - "$TMP_ROOT/readyz.body" <<'PY' 2>/dev/null || echo "PARSE_ERROR"
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as exc:
    print("PARSE_ERROR", exc)
    raise SystemExit(0)
comp = d.get("components") or {}
print("status=%s database=%s checkpoint_saver=%s" % (
    d.get("status"), comp.get("database"), comp.get("checkpoint_saver")))
PY
)"
echo "[fence] readyz: $READY_VERDICT"
if [[ "$READY_VERDICT" != *"status=ready"* ]] || \
   [[ "$READY_VERDICT" != *"database=True"* ]] || \
   [[ "$READY_VERDICT" != *"checkpoint_saver=True"* ]]; then
  echo "ERROR: /readyz components not all healthy (got: $READY_VERDICT)" >&2
  FAIL=1
fi

# ── log assertions ──────────────────────────────────────────────────────────
if grep -q "Application startup failed" "$LOG_FILE"; then
  echo "ERROR: 'Application startup failed' in boot log" >&2
  FAIL=1
fi
if grep -q "cannot parse.*as bool" "$LOG_FILE"; then
  echo "ERROR: bogus yaml dict-parse warning present" >&2
  FAIL=1
fi
# G1: post-apply truth - the apply line emitted BEFORE _set_config_value is
# not enough; assert the post-apply confirmation line (emitted only after
# the mutation landed) AND the absence of the fail-soft skip marker for
# this key.
if ! grep -q "limits.worker_pool_size: applied to config - field now" "$LOG_FILE"; then
  echo "ERROR: worker_pool_size post-apply confirmation line missing from boot log" >&2
  FAIL=1
fi
if grep -q "limits.worker_pool_size:.*boot apply skipped" "$LOG_FILE"; then
  echo "ERROR: worker_pool_size was skipped (fail-soft) instead of applied" >&2
  FAIL=1
fi
# G4: the seeded unknown-key row must produce the unregistered-row WARNING
# (proves the fail-soft path ran, not just that boot survived).
if ! grep -q "ignoring unregistered app_settings row" "$LOG_FILE"; then
  echo "ERROR: unregistered-row warning missing - fail-soft path did not run" >&2
  FAIL=1
fi
if ! grep -q "smoke_unknown_legacy_key" "$LOG_FILE"; then
  echo "ERROR: unknown-key row name missing from the unregistered-row warning" >&2
  FAIL=1
fi
if ! grep -q "\[fence\]" "$LOG_FILE"; then
  echo "ERROR: the [fence] preamble is missing from the boot log" >&2
  FAIL=1
fi

if [[ "$FAIL" -ne 0 ]]; then
  echo "--- log tail ---" >&2
  tail -40 "$LOG_FILE" >&2
  exit 1
fi

# ── teardown FIRST, then PASS (an exit-3 child must never print PASS) ──────
teardown_smoke
if [[ "$SMOKE_EXIT" == "3" ]]; then
  echo "ERROR: the smoke child exited with code 3 (startup crash)" >&2
  exit 1
fi
if ! port_released; then
  echo "ERROR: port $PORT still held after teardown — a daemon leaked" >&2
  exit 1
fi

echo "PASS: boot smoke OK (port=$PORT, db=$SMOKE_DB, child_exit=$SMOKE_EXIT, unit=${UNIT:-<none>})"
exit 0
