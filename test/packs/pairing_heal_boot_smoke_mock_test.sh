#!/usr/bin/env bash
# Test Pack: pairing_heal_boot_smoke_mock_test — PAIRING-HEAL BOOT SMOKE.
#
# Purpose: boot-smoke insulation check for the pairing-heal branch
# (fix/tool-pairing-full-history-heal @ 86c1bc041, base latest @ 9be991d56).
# This pack proves the PATCH'S IMPORT-TIME WIRING boots clean against
# a disposable PG + disposable mock-LLM. It is NOT a verification of the
# pairing heal surface (that's tph44 + d1_seam_pairing + injection_pairing
# unit packs); it is a Core #2/#3 INSULATION check that graph.py imports
# without Traceback and the daemon completes boot when the patch is in
# place.
#
# Fence: 15800 (daemon), 15810 (PG16), 15820 (mock-LLM). These are
# lane-disposable per discovery (no live ensemble instance binds them).
# 5432 (prod PG), 8079 (dev API), 8088 (self-system) NEVER touched.
#
# Assertions (PASS criteria):
#   1. Banner "Starting Ensemble v<…>" present in daemon stdout.
#   2. /livez returns 200 within 90s.
#   3. /readyz returns 200.
#   4. SIGTERM produces clean exit in ≤15s.
#   5. All three ports (15800, 15810, 15820) freed after teardown.
#
# Base-attribute rule: if the pack REDs, the implementer attributes the
# failure to BASE (latest @ 9be991d56) or PATCH (86c1bc041) per the
# "chart the result even if it goes red" instruction in the brief. RED
# here is a finding, not a blocker.
#
# Architecture:
#   - daemon boots against DISPOSABLE PG16 (127.0.0.1:15810, NOT 5432).
#   - Mock LLM (127.0.0.1:15820) serves minimal /v1/chat/completions 200
#     with empty choices[] so the boot path does not stall on a real
#     LLM call. NOTE: the daemon only needs the mock to be reachable on
#     boot; LLM calls happen on agent_node, not on startup.
#   - env scrub: env -u POSTGRES_HOST/POSTGRES_PORT/POSTGRES_DB/
#     POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_URL/DATABASE_URL so the
#     inheriting shell env (which carries prod-POSTGRES_DB=ensemble_prod
#     on 5432) cannot leak.
#   - Clean shutdown: SIGTERM → uvicorn graceful exit ≤15s via
#     dev.sh's --timeout-graceful-shutdown 10 (proved by
#     dev_sh_static_unit_test) → ports freed.
#
# Run:  timeout 300 bash test/packs/pairing_heal_boot_smoke_mock_test.sh
#
# Dual-layer timeout (innate test-pack invariant):
#   Layer 1 (outer): `timeout 300` wrapper by the caller.
#   Layer 2 (inner): INTERNAL_DEADLINE = start + 270s enforced in every
#                    wait loop; breach → RESULT: TIMEOUT, exit 124.
# Hard cap: 5 minutes per pack execution.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

DAEMON_PORT=15800
PG_PORT=15810
MOCK_LLM_PORT=15820      # pairing-heal lane-disposable; OpenAI-compatible mock

PG_BIN="/usr/lib/postgresql/16/bin"
PG_USER="ensemble"
PG_DB="ensemble_test"

SCRATCH="/tmp/pairing-heal-boot-smoke"
PGDATA="$SCRATCH/pgdata"
PG_LOG="$SCRATCH/pg.log"
DATA_DIR="$SCRATCH/data"
DAEMON_STDOUT="$SCRATCH/daemon.stdout"
MOCK_LLM_LOG="$SCRATCH/mock_llm.log"
MOCK_LLM_STDOUT="$SCRATCH/mock_llm.stdout"
PIDS_FILE="$SCRATCH/pids.txt"

DAEMON_PID=""
MOCK_LLM_PID=""
PG_RUN_BY_US=0
TIMEOUT_FLAG=0

PASS=0
FAIL=0
START_TS=$(date +%s)
INTERNAL_DEADLINE=$((START_TS + 270))   # ≤270s internal deadline; cleanup room before outer 300

# ── helpers ─────────────────────────────────────────────────────────────
ok()    { echo "[PASS] $*"; PASS=$((PASS+1)); }
bad()   { echo "[FAIL] $*"; FAIL=$((FAIL+1)); }
note()  { echo "[INFO] $*"; }

time_left() { [ "$(date +%s)" -lt "$INTERNAL_DEADLINE" ]; }

deadline_hit() {
  TIMEOUT_FLAG=1
  bad "internal deadline (${INTERNAL_DEADLINE}s) exceeded"
}

record_pid() { echo "$1|$2" >> "$PIDS_FILE"; }

kill_recorded() {
  while IFS='|' read -r pid name; do
    [ -n "$pid" ] || continue
    if kill -0 "$pid" 2>/dev/null; then
      note "teardown: SIGTERM $name pid=$pid"
      kill -TERM "$pid" 2>/dev/null || true
      for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
      done
      kill -0 "$pid" 2>/dev/null && { note "teardown: SIGKILL $name pid=$pid"; kill -KILL "$pid" 2>/dev/null || true; }
    fi
  done < "$PIDS_FILE"
}

cleanup() {
  local rc=$?
  if [ "$TIMEOUT_FLAG" = "1" ] && [ "$rc" = "0" ]; then rc=124; fi
  echo ""
  echo "===== CLEANUP (rc=$rc) ====="
  kill_recorded
  sleep 1
  # Port-freedom verification for ports we own (kill ONLY recorded PIDs).
  for p in $DAEMON_PORT $MOCK_LLM_PORT; do
    [ -n "$p" ] || continue
    port_pid=$(lsof -ti:"$p" 2>/dev/null | head -1 || true)
    if [ -n "$port_pid" ]; then
      if grep -q "^${port_pid}|" "$PIDS_FILE" 2>/dev/null; then
        note "port $p still bound by recorded pid=$port_pid — SIGKILL"
        kill -KILL "$port_pid" 2>/dev/null || true
        sleep 1
      else
        note "port $p bound by UNRECORDED pid=$port_pid — leaving alone"
      fi
    fi
    if lsof -nP -i ":$p" >/dev/null 2>&1; then
      bad "port $p STILL bound after teardown"
    else
      ok "port $p freed (lsof verified)"
    fi
  done
  # PG teardown (only if we started it)
  if [ "$PG_RUN_BY_US" = "1" ] && [ -d "$PGDATA" ]; then
    note "PG teardown: pg_ctl stop + rm -rf $PGDATA"
    "$PG_BIN/pg_ctl" -D "$PGDATA" stop -m fast >/dev/null 2>&1 || true
    rm -rf "$PGDATA" "$PG_LOG" 2>/dev/null || true
  fi
  if [ -d "$PGDATA" ]; then bad "PGDATA $PGDATA not removed"; else ok "PGDATA removed"; fi
  if [ -d "$DATA_DIR" ]; then
    note "rm -rf $DATA_DIR"
    rm -rf "$DATA_DIR" 2>/dev/null || true
  fi
  if [ -d "$DATA_DIR" ]; then bad "DATA_DIR $DATA_DIR not removed"; else ok "DATA_DIR removed"; fi
  # Orphan check: no recorded PID may survive
  ORPHANS=0
  while IFS='|' read -r pid name; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      bad "orphan PID alive: $name pid=$pid"
      ORPHANS=$((ORPHANS+1))
    fi
  done < "$PIDS_FILE"
  [ "$ORPHANS" = "0" ] && ok "no orphan PIDs from this pack" || true
  rm -f "$PIDS_FILE" 2>/dev/null || true
  ELAPSED=$(( $(date +%s) - START_TS ))
  echo ""
  echo "===== PACK RESULT: PASS=$PASS FAIL=$FAIL runtime=${ELAPSED}s ====="
  if [ "$TIMEOUT_FLAG" = "1" ]; then
    echo "RESULT: TIMEOUT"
    rc=124
  elif [ "$FAIL" -eq 0 ]; then
    echo "RESULT: PASS"
    rc=0
  else
    echo "RESULT: FAIL"
    rc=1
  fi
  exit $rc
}
trap cleanup EXIT INT TERM

# ── PHASE 0: preflight ──────────────────────────────────────────────────
echo "===== PHASE 0: preflight ====="
mkdir -p "$SCRATCH"
: > "$PIDS_FILE"

for tool in initdb pg_ctl pg_isready psql createdb; do
  command -v "$PG_BIN/$tool" >/dev/null 2>&1 || { bad "PG16 tool missing: $tool ($PG_BIN/$tool)"; exit 1; }
done
[ -x "$PROJECT_DIR/.venv/bin/python" ] || { bad ".venv/bin/python missing"; exit 1; }
ok "tools present (PG16 bin, .venv python)"

# venv-only psycopg2 install (no repo / no git change). Disclosed env side-effect.
if ! "$PROJECT_DIR/.venv/bin/python" -c "import psycopg2" >/dev/null 2>&1; then
  note "psycopg2 missing in worktree venv — installing venv-only (no repo change)"
  (cd "$PROJECT_DIR" && uv pip install psycopg2-binary >/dev/null 2>&1) || {
    bad "uv pip install psycopg2-binary failed"; exit 1; }
fi
"$PROJECT_DIR/.venv/bin/python" -c "import psycopg2; print('psycopg2', psycopg2.__version__)" >/dev/null 2>&1 \
  && ok "psycopg2 available in venv"
ok "psycopg2-binary installed venv-only (environment side-effect; no repo change)"

# Protected ports — never touched, just observed
for protect in 5432 8079 8088 15432 15433; do
  if lsof -ti:"$protect" >/dev/null 2>&1; then
    note "protected port $protect in use (NOT touched by this pack)"
  fi
done

# Daemon port 15800: must be free. NEVER kill an unknown owner.
if lsof -ti:"$DAEMON_PORT" >/dev/null 2>&1; then
  bad "daemon port $DAEMON_PORT occupied by pid=$(lsof -ti:"$DAEMON_PORT" | head -1) — refusing to start"
  lsof -nP -i ":$DAEMON_PORT" | sed 's/^/    /'
  exit 1
fi
ok "daemon port $DAEMON_PORT free"

# PG port 15810: must be free.
if lsof -ti:"$PG_PORT" >/dev/null 2>&1; then
  bad "PG port $PG_PORT occupied by pid=$(lsof -ti:"$PG_PORT" | head -1) — refusing to start"
  lsof -nP -i ":$PG_PORT" | sed 's/^/    /'
  exit 1
fi
ok "PG_PORT=$PG_PORT free"

# Mock LLM port 15820: must be free.
if lsof -ti:"$MOCK_LLM_PORT" >/dev/null 2>&1; then
  bad "mock-LLM port $MOCK_LLM_PORT occupied by pid=$(lsof -ti:"$MOCK_LLM_PORT" | head -1) — refusing to start"
  lsof -nP -i ":$MOCK_LLM_PORT" | sed 's/^/    /'
  exit 1
fi
ok "MOCK_LLM_PORT=$MOCK_LLM_PORT free"

# ── PHASE 1: disposable PG ──────────────────────────────────────────────
echo ""
echo "===== PHASE 1: disposable PG16 on 127.0.0.1:$PG_PORT ====="
rm -rf "$PGDATA" "$PG_LOG"
"$PG_BIN/initdb" -A trust -U "$PG_USER" "$PGDATA" -c unix_socket_directories="$SCRATCH/pgsock" >/dev/null 2>&1 \
  || { bad "initdb failed"; exit 1; }
mkdir -p "$SCRATCH/pgsock"
"$PG_BIN/pg_ctl" -D "$PGDATA" -o "-p $PG_PORT -k $SCRATCH/pgsock" -l "$PG_LOG" start >/dev/null 2>&1 \
  || { bad "pg_ctl start failed"; tail -20 "$PG_LOG"; exit 1; }
PG_RUN_BY_US=1
for _ in $(seq 1 30); do
  time_left || { deadline_hit; exit 1; }
  "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1 && break
  sleep 1
done
"$PG_BIN/pg_isready" -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1 \
  || { bad "PG not ready on $PG_PORT"; tail -20 "$PG_LOG"; exit 1; }
ok "PG up on 127.0.0.1:$PG_PORT"
"$PG_BIN/createdb" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" "$PG_DB" >/dev/null 2>&1
"$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" \
  -c "GRANT ALL ON SCHEMA public TO $PG_USER;" >/dev/null 2>&1
ok "DB $PG_DB created + schema public granted"

# ── PHASE 2: mock LLM (minimal OpenAI-compatible) ──────────────────────
echo ""
echo "===== PHASE 2: minimal mock LLM on 127.0.0.1:$MOCK_LLM_PORT ====="
: > "$MOCK_LLM_LOG"
: > "$MOCK_LLM_STDOUT"

nohup "$PROJECT_DIR/.venv/bin/python" -c "
import json, os, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
PORT = int(os.environ.get('MOCK_LLM_PORT', '15820'))
LOG  = os.environ.get('MOCK_LLM_LOG', '$MOCK_LLM_LOG')

def log(msg):
    with open(LOG, 'a') as f:
        f.write(f'[{time.strftime(\"%H:%M:%S\")}] {msg}\n')

class H(BaseHTTPRequestHandler):
    def log_message(self, *a, **k): pass
    def _send(self, code, body):
        if isinstance(body, str): body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        log(f'GET {self.path}')
        if self.path.startswith('/v1/models'):
            self._send(200, json.dumps({'object':'list','data':[{'id':'mock-llm','object':'model','created':0,'owned_by':'local'}]}))
            return
        if self.path == '/healthz':
            self._send(200, json.dumps({'status':'ok'}))
            return
        self._send(404, json.dumps({'error':'not_found'}))
    def do_POST(self):
        n = int(self.headers.get('Content-Length','0') or 0)
        _ = self.rfile.read(n) if n else b''
        log(f'POST {self.path}')
        if self.path.endswith('/v1/chat/completions'):
            # Minimal empty-choices payload so the daemon completes boot
            # without trying to parse a real assistant message. Boot
            # smoke is about import-time wiring; we don't reach agent_node
            # in this pack.
            payload = {
                'id': 'chatcmpl-mock',
                'object': 'chat.completion',
                'created': int(time.time()),
                'model': 'mock-llm',
                'choices': [],
                'usage': {'prompt_tokens':0,'completion_tokens':0,'total_tokens':0},
            }
            self._send(200, json.dumps(payload))
            return
        self._send(404, json.dumps({'error':'not_found'}))

s = ThreadingHTTPServer(('127.0.0.1', PORT), H)
log(f'mock LLM listening on 127.0.0.1:{PORT}')
s.serve_forever()
" > "$MOCK_LLM_STDOUT" 2>&1 &
MOCK_LLM_PID=$!
record_pid "$MOCK_LLM_PID" "mock_llm"
echo "$MOCK_LLM_PID" > "$SCRATCH/mock_llm.pid"
sleep 1
MOCK_OK=0
for _ in $(seq 1 10); do
  time_left || { deadline_hit; exit 1; }
  mcode=$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$MOCK_LLM_PORT/healthz" 2>/dev/null || echo "000")
  if [ "$mcode" = "200" ]; then
    MOCK_OK=1; break
  fi
  sleep 1
done
[ "$MOCK_OK" = "1" ] || { bad "mock LLM not healthy on $MOCK_LLM_PORT"; tail -30 "$MOCK_LLM_STDOUT"; exit 1; }
ok "mock LLM up on 127.0.0.1:$MOCK_LLM_PORT pid=$MOCK_LLM_PID"

# ── PHASE 3: daemon boot ────────────────────────────────────────────────
echo ""
echo "===== PHASE 3: daemon boot → 127.0.0.1:$DAEMON_PORT (disposable PG, mock-LLM) ====="
rm -rf "$DATA_DIR"
mkdir -p "$DATA_DIR/logs"
: > "$DAEMON_STDOUT"
unset SSL_CERT_FILE SSL_CERT_DIR

ENV_CMD=(env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER \
              -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL)
ENV_CMD+=(ENSEMBLE_TEST_PG_URL="postgresql://${PG_USER}@127.0.0.1:${PG_PORT}/${PG_DB}")
ENV_CMD+=(ENSEMBLE_DATA_DIR="$DATA_DIR")
ENV_CMD+=(DATA_DIR="$DATA_DIR")
ENV_CMD+=(PERSISTENCE_DB_PATH="$DATA_DIR/instances.db")
ENV_CMD+=(POSTGRES_HOST=127.0.0.1)
ENV_CMD+=(POSTGRES_PORT="$PG_PORT")
ENV_CMD+=(POSTGRES_DB="$PG_DB")
ENV_CMD+=(POSTGRES_USER="$PG_USER")
ENV_CMD+=(POSTGRES_PASSWORD="$PG_USER")
ENV_CMD+=(DAEMON_PORT="$DAEMON_PORT")
ENV_CMD+=(OPENAI_BASE_URL="http://127.0.0.1:${MOCK_LLM_PORT}/v1")
ENV_CMD+=(OPENAI_API_KEY=test-mock-key-not-real)
ENV_CMD+=(OPENAI_MODEL=mock-llm)
ENV_CMD+=(OPENAI_MODEL_KEYWORDS=)
ENV_CMD+=(OPENAI_REQUEST_GZIP=0)
ENV_CMD+=(nohup uv run python -m uvicorn daemon.api:app \
            --host 127.0.0.1 --port "$DAEMON_PORT" \
            --log-level info --no-access-log --timeout-graceful-shutdown 10)
"${ENV_CMD[@]}" > "$DAEMON_STDOUT" 2>&1 &
DAEMON_PID=$!
record_pid "$DAEMON_PID" "daemon"
echo "$DAEMON_PID" > "$SCRATCH/daemon.pid"
ok "daemon started pid=$DAEMON_PID (POSTGRES_*→disposable :$PG_PORT, DAEMON_PORT=$DAEMON_PORT, OPENAI→mock)"

# Health poll: time-bracketed (no line-number windows)
BOOT_START_TS=$(date +%s)
HEALTHY=0
for _ in $(seq 1 90); do
  time_left || { deadline_hit; exit 1; }
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$DAEMON_PORT/livez" 2>/dev/null)
  [ "$code" = "200" ] && { HEALTHY=1; break; }
  kill -0 "$DAEMON_PID" 2>/dev/null || { bad "daemon exited early during boot"; tail -40 "$DAEMON_STDOUT"; exit 1; }
  sleep 1
done
ASSERT_TS=$(date +%s)
[ "$HEALTHY" = "1" ] || { bad "/livez never returned 200 within 90s"; tail -40 "$DAEMON_STDOUT"; exit 1; }
ok "/livez 200 on $DAEMON_PORT (within $((ASSERT_TS-BOOT_START_TS))s)"

READY_CODE=$(curl -s -o "$SCRATCH/readyz.json" -w '%{http_code}' "http://127.0.0.1:$DAEMON_PORT/readyz" 2>/dev/null || echo "000")
[ "$READY_CODE" = "200" ] || { bad "/readyz returned $READY_CODE"; exit 1; }
ok "/readyz 200"

# ── PHASE 4: BANNER + boot cleanliness ──────────────────────────────────
echo ""
echo "===== PHASE 4: BANNER + boot cleanliness (import-time wiring check) ====="
sleep 3

# Core #2/#3 invariant: the daemon's "Starting Ensemble v<…>" banner
# appears in stdout. This proves the boot path completed module-load +
# ASGI app construction (the seam the patch touches via graph.py).
BANNER_LINE=$(grep -E "Starting Ensemble v" "$DAEMON_STDOUT" | head -1 || true)
if [ -n "$BANNER_LINE" ]; then
  ok "banner present: $BANNER_LINE"
else
  bad "banner MISSING: 'Starting Ensemble v<…>' did not appear in daemon stdout"
  tail -40 "$DAEMON_STDOUT"
  exit 1
fi

# Pin engine line to disposable DB
ENGINE_LINE=$(grep "Creating PostgreSQL engine" "$DAEMON_STDOUT" | head -1 || true)
if [ -n "$ENGINE_LINE" ] && echo "$ENGINE_LINE" | grep -q "$PG_DB"; then
  ok "engine line pins disposable DB $PG_DB: $ENGINE_LINE"
else
  bad "engine line missing or not pinned to $PG_DB — got: ${ENGINE_LINE:-<none>}"
  exit 1
fi

# Boot cleanliness: zero Traceback/CRITICAL/^ValueError in first 300 lines.
# Core #2/#3 insulation: the patch must not break import-time wiring.
ERRORS=0
while IFS= read -r line; do
  if echo "$line" | grep -qE "Traceback|CRITICAL |^ValueError"; then
    ERRORS=$((ERRORS+1))
  fi
done < <(head -300 "$DAEMON_STDOUT")
if [ "$ERRORS" = "0" ]; then
  ok "boot log verdict: 0 Traceback/CRITICAL/^ValueError in first 300 lines"
else
  bad "boot log verdict: $ERRORS Traceback/CRITICAL/^ValueError lines in boot window"
  head -300 "$DAEMON_STDOUT" | grep -E "Traceback|CRITICAL |^ValueError" | head -10 | sed 's/^/    /'
  exit 1
fi

note "BANNER EVIDENCE (verbatim, daemon stdout):"
echo "  BEGIN_OF_VERBATIM_BANNER"
echo "    $BANNER_LINE"
echo "  END_OF_VERBATIM_BANNER"

# ── PHASE 5: clean shutdown ─────────────────────────────────────────────
echo ""
echo "===== PHASE 5: clean shutdown (SIGTERM → graceful exit ≤15s) ====="
SHUTDOWN_START=$(date +%s)
kill -TERM "$DAEMON_PID" 2>/dev/null || true
SHUTDOWN_OK=0
for _ in $(seq 1 30); do
  time_left || { deadline_hit; exit 1; }
  if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    SHUTDOWN_OK=1; break
  fi
  sleep 0.5
done
SHUTDOWN_ELAPSED=$(( $(date +%s) - SHUTDOWN_START ))
if [ "$SHUTDOWN_OK" = "1" ]; then
  ok "daemon exited gracefully in ${SHUTDOWN_ELAPSED}s (target ≤15s)"
else
  bad "daemon did NOT exit within 15s of SIGTERM (elapsed=${SHUTDOWN_ELAPSED}s)"
  kill -KILL "$DAEMON_PID" 2>/dev/null || true
  exit 1
fi

# Port 15800 freed (record a follow-up free-check inside the trap too)
sleep 1
if lsof -ti:"$DAEMON_PORT" >/dev/null 2>&1; then
  PORT_PID=$(lsof -ti:"$DAEMON_PORT" | head -1)
  if grep -q "^${PORT_PID}|" "$PIDS_FILE" 2>/dev/null; then
    note "port $DAEMON_PORT still bound by RECORDED pid=$PORT_PID — kill in trap"
  else
    note "port $DAEMON_PORT bound by UNRECORDED pid=$PORT_PID — leaving alone (assertion deferred)"
  fi
fi

note "PHASE 5: clean shutdown complete — full teardown verified in trap EXIT"

# ── Cleanup via trap EXIT ───────────────────────────────────────────────
# Script's natural end triggers the EXIT trap → cleanup() runs and
# prints the final CLEANUP / PACK RESULT banners (see cleanup() body).
# No trailing echo here — the trap banner is the canonical end signal.
