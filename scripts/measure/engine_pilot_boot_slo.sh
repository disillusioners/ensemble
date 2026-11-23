#!/usr/bin/env bash
# =============================================================================
# engine_pilot_boot_slo.sh — ISOLATED boot→readyz-green measurement for the
# OrphanWatcher engine pilot (Phase-2 evidence lane, 2026-11-23).
#
# WHAT IT MEASURES
#   1. boot → /livez green  (seconds)
#   2. boot → /readyz green (seconds)  ← the Emperor's binding boot SLO is
#      boot_epoch → readyz-green ≤ 120 s
#   3. the ENGINE BOOT BLOCK's gated cost, read from the daemon's OWN posture
#      line: block_elapsed_ms_gated (the SLO-1 ≤250 ms cost bound the flip
#      decision depends on) and block_elapsed_ms_total (the ungated total).
#
# IT EMITS ONE JSONL RECORD PER BOOT (kind=boot_slo_measurement) — that file
# is the input to the soak harness (scripts/soak/engine_pilot_orphan_soak.py
# --boot-telemetry), which is where the 48 h window is judged.
#
# USAGE
#   scripts/measure/engine_pilot_boot_slo.sh --source <tree>   [options]
#   scripts/measure/engine_pilot_boot_slo.sh --release <dir>   [options]
#     --port <n>          explicit port (must NOT be 9797/9798/8079)
#     --db <name>         database name (default: ensemble_dev)
#     --timeout <secs>    hard boot deadline (default 120)
#     --out <file>        JSONL output (default: stdout)
#     --label <text>      free-form label carried in the record
#
# THE FENCE (house recipe — every leg of it is load-bearing)
# =============================================================================
#  1. ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 is PINNED EXPLICITLY. It is
#     NEVER `env -u`'d: the frozen-binary boot sweep KILLS LIVE processes
#     when the guard is absent (incident 2026-10-31 — it SIGTERM/SIGKILLed a
#     live launcher+ensemble-prod pair). `env -u` on it strips the ambient
#     guard; pinning 0 is the safe form.
#  2. THE ENV IS SCRUBBED ONE VARIABLE AT A TIME. `env -u POSTGRES_*` is a
#     SILENT NO-OP in bash (it does not glob env names) — an ambient
#     POSTGRES_DB=ensemble_prod stays visible and a "fenced" run can still
#     hit prod. Every name is scrubbed explicitly, and the fence is ASSERTED
#     in the boot log before exec.
#  3. NEVER 9797 / 9798 (the live daemon ports) and never 8079 (the dev
#     worktree's fence port). The house range is 19100-19999 and the port is
#     proven free pre-bind.
#  4. The daemon is booted as a child of THIS shell; only the RECORDED pid
#     is signalled (SIGTERM → wait → SIGKILL), and the port release is
#     ASSERTED. The live daemon is never touched.
#  5. The working tree is never the source of the live install: --release
#     boots an ISOLATED COPY under <tmproot>/releases/<name>.
# =============================================================================
set -u

SOURCE_TREE=""
RELEASE_DIR=""
PORT=""
DB_NAME="ensemble_dev"
TIMEOUT=120
OUT=""
LABEL=""
ENGINE_FLAG=""
SHADOW_MODE=""
ENGINE_ENABLED=""

usage() {
  sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE_TREE="$2"; shift 2 ;;
    --release) RELEASE_DIR="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --db) DB_NAME="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --engine-flag) ENGINE_FLAG="$2"; shift 2 ;;
    --shadow-mode) SHADOW_MODE="$2"; shift 2 ;;
    --engine-enabled) ENGINE_ENABLED="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "UNKNOWN ARG: $1" >&2; usage ;;
  esac
done

if [ -z "$SOURCE_TREE" ] && [ -z "$RELEASE_DIR" ]; then
  echo "FATAL: one of --source <tree> or --release <dir> is required" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── port selection (never 9797/9798/8079) ────────────────────────────────
port_free() {
  ! (command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -qE "[:.]$1[[:space:]]") \
    && ! (command -v lsof >/dev/null 2>&1 && lsof -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1)
}
if [ -z "$PORT" ]; then
  for cand in $(seq 19100 19199); do
    if port_free "$cand"; then PORT="$cand"; break; fi
  done
fi
if [ -z "$PORT" ]; then
  echo "FATAL: no free port in the house range 19100-19199" >&2
  exit 2
fi
case "$PORT" in
  9797|9798|8079)
    echo "FATAL: refusing to boot on a forbidden port ($PORT)" >&2
    exit 2 ;;
esac

# ── the thing to boot ────────────────────────────────────────────────────
TMP_ROOT="$(mktemp -d)"
BOOT_CWD=""
BOOT_CMD=""
if [ -n "$RELEASE_DIR" ]; then
  [ -d "$RELEASE_DIR" ] || { echo "FATAL: --release dir not found: $RELEASE_DIR" >&2; exit 2; }
  REL_NAME="$(basename "$RELEASE_DIR")"
  BOOT_CWD="$TMP_ROOT/releases/$REL_NAME"
  mkdir -p "$TMP_ROOT/releases"
  cp -a "$RELEASE_DIR" "$BOOT_CWD"
  BIN="$(find "$BOOT_CWD" -maxdepth 1 -type f -perm -u+x ! -name '*.yaml' ! -name '*.json' ! -name '*.md' | head -1)"
  if [ -z "$BIN" ]; then
    echo "FATAL: no executable found in the release dir: $BOOT_CWD" >&2
    exit 2
  fi
  BOOT_CMD="$BIN"
else
  BOOT_CWD="$SOURCE_TREE"
  [ -d "$BOOT_CWD" ] || { echo "FATAL: --source tree not found: $BOOT_CWD" >&2; exit 2; }
  if [ -x "$BOOT_CWD/.venv/bin/python" ]; then
    BOOT_CMD="$BOOT_CWD/.venv/bin/python -m daemon"
  else
    echo "FATAL: no .venv in $BOOT_CWD (worktrees need their own venv)" >&2
    exit 2
  fi
fi

LOG="$TMP_ROOT/boot.log"
READY_BODY="$TMP_ROOT/readyz.json"
LIVEZ_BODY="$TMP_ROOT/livez.json"
mkdir -p "$BOOT_CWD/data_dev"

# ── the fence (scrubbed ONE VARIABLE AT A TIME) ──────────────────────────
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u PGHOST -u PGUSER -u PGPASSWORD -u PGPORT -u PGDATABASE
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u ENSEMBLE_ENGINE_SHADOW_MODE -u ENSEMBLE_ENGINE_ENABLED
  -u SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE
)

# The dev credentials come from the tree's OWN .env (the worktree fence).
ENV_FILE="$BOOT_CWD/.env"
if [ ! -f "$ENV_FILE" ] && [ -f "$PROJECT_DIR/.env" ]; then
  ENV_FILE="$PROJECT_DIR/.env"
fi

cleanup() {
  if [ -s "$PID_FILE" ]; then
    BPID="$(cat "$PID_FILE")"
    if kill -0 "$BPID" 2>/dev/null; then
      kill -TERM "$BPID" 2>/dev/null
      for _ in $(seq 1 10); do kill -0 "$BPID" 2>/dev/null || break; sleep 1; done
      if kill -0 "$BPID" 2>/dev/null; then kill -KILL "$BPID" 2>/dev/null; sleep 1; fi
    fi
  fi
  rm -rf "$TMP_ROOT"
}
PID_FILE="$(mktemp)"
trap cleanup EXIT

# ── launch ───────────────────────────────────────────────────────────────
# ORDER MATTERS: the ambient environment is scrubbed FIRST (per-variable),
# and the tree's OWN fenced .env is sourced INSIDE that scrubbed child, so
# the dev credentials survive while the ambient prod-shaped values do not.
# (Scrubbing after sourcing .env would strip POSTGRES_USER/PASSWORD and the
# boot would silently fall back to the config.yaml defaults.)
#
# The inner boot script is WRITTEN OUT (no nested quoting) so the fence
# assertions are readable and cannot be broken by shell quoting.
INNER="$TMP_ROOT/boot_inner.sh"
cat > "$INNER" <<'INNER_EOF'
#!/usr/bin/env bash
set -u
set -a
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
set +a
export PORT DAEMON_PORT POSTGRES_HOST POSTGRES_PORT POSTGRES_DB DB_NAME
export ENSEMBLE_SELF_ENV ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES
export ENSEMBLE_DB_AMBIENT_ALLOW DATA_DIR ENSEMBLE_DATA_DIR
export PERSISTENCE_DB_PATH PYTHONDONTWRITEBYTECODE
# The pilot switches are applied ONLY when explicitly requested (the scrub
# above strips them otherwise). Empty ⇒ the variable stays UNSET — which is
# the shipped dormant posture (the flag family is never set by the product).
[ -n "$ENGINE_FLAG" ] && export SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE="$ENGINE_FLAG"
[ -n "$SHADOW_MODE" ] && export ENSEMBLE_ENGINE_SHADOW_MODE="$SHADOW_MODE"
[ -n "$ENGINE_ENABLED" ] && export ENSEMBLE_ENGINE_ENABLED="$ENGINE_ENABLED"
# FENCE ASSERTIONS — fail LOUD, before exec.
[ "$POSTGRES_DB" != "ensemble_prod" ] || { echo "FENCE_VIOLATION=DB_IS_PROD" >&2; exit 97; }
[ "$POSTGRES_DB" = "$DB_NAME" ] || { echo "FENCE_VIOLATION=DB_MISMATCH" >&2; exit 97; }
[ "$ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES" = "0" ] || { echo "FENCE_VIOLATION=SWEEP_GUARD" >&2; exit 97; }
case "$PORT" in 9797|9798|8079) echo "FENCE_VIOLATION=PORT_FORBIDDEN" >&2; exit 97;; esac
echo "FENCE_ASSERT=PASS db=$POSTGRES_DB port=$PORT sweep_guard=$ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"
cd "$BOOT_CWD" || exit 96
exec "$@"
INNER_EOF
chmod +x "$INNER"

(
  env "${SCRUB[@]}" \
    ENV_FILE="$ENV_FILE" \
    BOOT_CWD="$BOOT_CWD" \
    PORT="$PORT" DAEMON_PORT="$PORT" \
    POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB="$DB_NAME" \
    DB_NAME="$DB_NAME" \
    ENGINE_FLAG="$ENGINE_FLAG" SHADOW_MODE="$SHADOW_MODE" ENGINE_ENABLED="$ENGINE_ENABLED" \
    ENSEMBLE_SELF_ENV=dev \
    ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
    ENSEMBLE_DB_AMBIENT_ALLOW=1 \
    DATA_DIR=./data_dev ENSEMBLE_DATA_DIR=./data_dev \
    PERSISTENCE_DB_PATH=./data_dev/instances.db \
    PYTHONDONTWRITEBYTECODE=1 \
    bash "$INNER" $BOOT_CMD
) > "$LOG" 2>&1 &
BOOT_PID=$!
echo "$BOOT_PID" > "$PID_FILE"
START_MS=$(date +%s%3N)

# ── fence assertions (echoed into the log; secrets never echoed) ─────────
{
  echo "=== engine-pilot boot-SLO measurement ==="
  echo "boot_cwd=$BOOT_CWD"
  echo "boot_cmd=$BOOT_CMD"
  echo "port=$PORT db=$DB_NAME timeout_s=$TIMEOUT"
  echo "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 (PINNED EXPLICITLY)"
  echo "POSTGRES_DB=$DB_NAME POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432"
  echo "PORT=$PORT DAEMON_PORT=$PORT ENSEMBLE_SELF_ENV=dev"
  echo "scrubbed: POSTGRES_*/DATABASE_URL/ENSEMBLE_DB_DSN/PG*/SSL_*/engine switches (per-variable)"
} >> "$LOG"

# ── poll livez then readyz ───────────────────────────────────────────────
LIVEZ_AT=""; READYZ_AT=""
DEADLINE_S=$(( $(date +%s) + TIMEOUT ))
for i in $(seq 1 70); do
  sleep 2
  NOW_MS=$(date +%s%3N)
  if [ -z "$LIVEZ_AT" ]; then
    if curl -sf -m 3 "http://127.0.0.1:${PORT}/livez" -o /dev/null 2>/dev/null; then
      LIVEZ_AT=$((NOW_MS - START_MS))
      echo "PROBE livez GREEN at +${LIVEZ_AT}ms" >> "$LOG"
    fi
  fi
  if [ -n "$LIVEZ_AT" ] && [ -z "$READYZ_AT" ]; then
    if curl -sf -m 5 "http://127.0.0.1:${PORT}/readyz" -o "$READY_BODY" 2>/dev/null; then
      READYZ_AT=$((NOW_MS - START_MS))
      echo "PROBE readyz GREEN at +${READYZ_AT}ms" >> "$LOG"
      # F3 — capture the daemon's OWN view at the green instant:
      # /livez uptime_seconds + the readyz composite's checked_at.
      curl -sf -m 3 "http://127.0.0.1:${PORT}/livez" -o "$LIVEZ_BODY" 2>/dev/null || true
    fi
  fi
  [ -n "$READYZ_AT" ] && break
  if ! kill -0 "$BOOT_PID" 2>/dev/null; then
    echo "PROBE daemon died before readyz" >> "$LOG"
    break
  fi
  [ "$(date +%s)" -ge "$DEADLINE_S" ] && { echo "PROBE hard deadline reached" >> "$LOG"; break; }
done

POSTURE="$(grep -h 'engine boot posture' "$LOG" 2>/dev/null | head -1)"
GATED="$(printf '%s' "$POSTURE" | grep -oE 'block_elapsed_ms_gated=[0-9.]+' | head -1 | cut -d= -f2)"
TOTAL="$(printf '%s' "$POSTURE" | grep -oE 'block_elapsed_ms_total=[0-9.]+' | head -1 | cut -d= -f2)"
EXECS="$(printf '%s' "$POSTURE" | grep -oE 'executable_specs=[0-9]+' | head -1 | cut -d= -f2)"
BOOT_EPOCH_LINE="$(grep -h 'Boot epoch captured' "$LOG" 2>/dev/null | head -1)"
BOOT_EPOCH_TS="$(printf '%s' "$BOOT_EPOCH_LINE" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+' | head -1)"

LIVEZ_S=""; READYZ_S=""
[ -n "$LIVEZ_AT" ] && LIVEZ_S="$(awk "BEGIN{printf \"%.3f\", $LIVEZ_AT/1000}")"
[ -n "$READYZ_AT" ] && READYZ_S="$(awk "BEGIN{printf \"%.3f\", $READYZ_AT/1000}")"

# ── F3 — TWO DISTINCT NUMBERS ──────────────────────────────────────────────
#   (a) launch -> readyz-green      : the HARNESS stopwatch. It is an
#       UPPER BOUND on the Emperor's SLO (the daemon's boot epoch is
#       captured AFTER the launch, so boot_epoch->readyz <= launch->readyz).
#   (b) daemon-reported boot elapsed: boot_epoch -> the readiness
#       composite's own `checked_at` — BOTH numbers are the DAEMON's, and
#       this is the SLO-ANCHORED measure (the Emperor's 120 s SLO is
#       boot_epoch -> readyz).
# The verdict is taken on the SLO-ANCHORED number when it is available,
# else on the (conservative) upper bound — and the basis is always reported.
READY_CHECKED_AT=""
if [ -s "$READY_BODY" ]; then
  READY_CHECKED_AT="$(grep -oE '"checked_at"[[:space:]]*:[[:space:]]*"[^"]*"' "$READY_BODY" | head -1 | grep -oE '"[^"]*"$' | tr -d '"')"
fi
LIVEZ_UPTIME=""
if [ -s "$LIVEZ_BODY" ]; then
  LIVEZ_UPTIME="$(grep -oE '"uptime_seconds"[[:space:]]*:[[:space:]]*[0-9.]+' "$LIVEZ_BODY" | head -1 | grep -oE '[0-9.]+$')"
fi
SLO_ANCHORED_S=""
if [ -n "$READY_CHECKED_AT" ] && [ -n "$BOOT_EPOCH_TS" ]; then
  _e_s="$(date -u -d "${BOOT_EPOCH_TS}+00:00" +%s 2>/dev/null || true)"
  _c_s="$(date -u -d "${READY_CHECKED_AT}" +%s 2>/dev/null || true)"
  if [ -n "$_e_s" ] && [ -n "$_c_s" ]; then
    SLO_ANCHORED_S="$(awk "BEGIN{printf \"%.3f\", $_c_s - $_e_s}")"
  fi
fi

SLO_BASIS="launch->readyz (UPPER BOUND - the daemon-reported number was unavailable)"
JUDGED_S="$READYZ_S"
if [ -n "$SLO_ANCHORED_S" ]; then
  SLO_BASIS="daemon-reported (boot_epoch -> readyz checked_at) - the SLO-anchored measure"
  JUDGED_S="$SLO_ANCHORED_S"
fi

VERDICT="VIOLATION"
if [ -z "$JUDGED_S" ]; then
  VERDICT="INCONCLUSIVE"
elif awk "BEGIN{exit !($JUDGED_S <= 120.0)}"; then
  VERDICT="PASS"
fi

RECORD=$(cat <<JSON
{"kind": "boot_slo_measurement", "label": "${LABEL}", "boot_cwd": "${BOOT_CWD}", "boot_cmd": "${BOOT_CMD}", "port": ${PORT}, "db": "${DB_NAME}", "boot_to_livez_seconds": ${LIVEZ_S:-null}, "boot_to_readyz_seconds": ${SLO_ANCHORED_S:-${READYZ_S:-null}}, "daemon_reported_boot_to_readyz_seconds": ${SLO_ANCHORED_S:-null}, "launch_to_readyz_seconds": ${READYZ_S:-null}, "daemon_uptime_at_readyz_seconds": ${LIVEZ_UPTIME:-null}, "readyz_checked_at": "${READY_CHECKED_AT}", "slo_basis": "${SLO_BASIS}", "boot_slo_seconds": 120.0, "block_elapsed_ms_gated": ${GATED:-null}, "block_elapsed_ms_total": ${TOTAL:-null}, "executable_specs": ${EXECS:-null}, "engine_flag": "${ENGINE_FLAG}", "shadow_mode": "${SHADOW_MODE}", "boot_epoch": "${BOOT_EPOCH_TS}", "verdict": "${VERDICT}", "log": "${LOG}"}
JSON
)

if [ -n "$OUT" ]; then
  echo "$RECORD" >> "$OUT"
  # Keep the boot log beside the measurement — the soak harness reads the
  # posture line (ownership evidence) and the boot epoch from it.
  cp "$LOG" "${OUT%.jsonl}.boot.log" 2>/dev/null || cp "$LOG" "$OUT.boot.log" 2>/dev/null || true
  echo "RECORD written to $OUT (verdict=$VERDICT)"
else
  echo "$RECORD"
fi
echo "--- posture line ---"
echo "${POSTURE:-<none>}"
echo "--- log tail ---"
tail -5 "$LOG" 2>/dev/null

case "$VERDICT" in
  PASS) exit 0 ;;
  INCONCLUSIVE) exit 4 ;;
  *) exit 3 ;;
esac
